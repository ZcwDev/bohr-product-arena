#!/usr/bin/env python3
"""A real, offline, file-based manuscript revision workflow.

The workorder asks for things that only exist if they are actually executed:
an import receipt, proposals decided one by one, version comparison, a real
rollback and reapplication, and controlled invalid operations that leave state
untouched. This implements them against files, so every claim in `result.json`
has a line in a log behind it.

Design rules:
  * the original paragraph text is never mutated - an annotation is a separate,
    clearly-labelled object bound to a target id, rendered alongside it;
  * a proposal whose quote is not verbatim in the target paragraph is refused
    (`quote_not_in_source`) instead of being softened into something plausible;
  * every invalid operation raises `InvalidOperation`, is appended to the
    receipt log, and the state file is written only after the check passes;
  * no step invents manuscript text: with no imported source, `propose` refuses.

CLI:
  python3 revise.py init      --blocks blocks.json --workdir DIR
  python3 revise.py propose   --workdir DIR --target C01-P002 --kind condition \\
                              --quote "..." --annotation "..." --why "..." [--uncertain]
  python3 revise.py decide    --workdir DIR --id R1 --accept | --reject --by NAME [--note "..."]
  python3 revise.py apply     --workdir DIR [--ids R1,R2]
  python3 revise.py rollback  --workdir DIR [--to v1]
  python3 revise.py reapply   --workdir DIR
  python3 revise.py diff      --workdir DIR --a v1 --b v2
  python3 revise.py status    --workdir DIR
  python3 revise.py export    --workdir DIR --outdir DIR
  python3 revise.py verify-state --workdir DIR   # prints the state hash
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import shutil
import sys
import time

STATE = "state.json"
RECEIPTS = "receipts.jsonl"

KINDS = ("condition", "limitation", "clarification", "terminology", "cross_reference")


class InvalidOperation(Exception):
    """A deliberately refused operation. `code` is stable and machine-readable."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _sha(obj) -> str:
    blob = json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def state_path(workdir: str) -> str:
    return os.path.join(workdir, STATE)


def load(workdir: str) -> dict:
    path = state_path(workdir)
    if not os.path.isfile(path):
        raise InvalidOperation("not_initialized", f"no {STATE} in {workdir}; run init first")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save(workdir: str, state: dict) -> None:
    state["state_hash"] = _sha({k: v for k, v in state.items() if k != "state_hash"})
    tmp = state_path(workdir) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, state_path(workdir))


def receipt(workdir: str, event: str, **fields) -> dict:
    record = {"ts": _now(), "event": event, **fields}
    with open(os.path.join(workdir, RECEIPTS), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


# ---------------------------------------------------------------- init

def cmd_init(args) -> int:
    os.makedirs(args.workdir, exist_ok=True)
    with open(args.blocks, encoding="utf-8") as fh:
        blocks_payload = json.load(fh)
    blocks = blocks_payload.get("blocks") or []
    state = {
        "created": _now(),
        "case_id": blocks_payload.get("case_id"),
        "ingest_status": blocks_payload.get("status"),
        "ingest_warnings": blocks_payload.get("warnings") or [],
        "sources": blocks_payload.get("sources") or [],
        "original": blocks,
        "versions": [{"name": "v1", "note": "imported original, unmodified",
                      "paragraphs": [b["id"] for b in blocks],
                      "applied": [], "created": _now()}],
        "current": "v1",
        "proposals": [],
        "next_proposal": 1,
    }
    save(args.workdir, state)
    rec = receipt(args.workdir, "import", blocks_file=os.path.abspath(args.blocks),
                  n_blocks=len(blocks), ingest_status=blocks_payload.get("status"),
                  sources=state["sources"])
    print(json.dumps({"workdir": args.workdir, "n_blocks": len(blocks),
                      "ingest_status": state["ingest_status"],
                      "state_hash": state["state_hash"],
                      "receipt": rec}, ensure_ascii=False, indent=1))
    return 0 if blocks else 1


# ---------------------------------------------------------------- propose

def _find_paragraph(state: dict, target: str) -> dict:
    for block in state["original"]:
        if block.get("id") == target:
            return block
    raise InvalidOperation("target_not_found",
                           f"{target!r} is not a paragraph id in the imported manuscript")


def cmd_propose(args) -> int:
    state = load(args.workdir)
    before = state["state_hash"]
    try:
        if not state["original"]:
            raise InvalidOperation("no_source", "no manuscript was imported; refusing to invent an annotation")
        if args.kind not in KINDS:
            raise InvalidOperation("bad_kind", f"kind must be one of {list(KINDS)}")
        block = _find_paragraph(state, args.target)
        text = block.get("text") or ""
        if not args.quote or args.quote not in text:
            raise InvalidOperation(
                "quote_not_in_source",
                f"quote is not verbatim inside {args.target}; an annotation must quote the paragraph it annotates")
        if not args.annotation.strip():
            raise InvalidOperation("empty_annotation", "an empty annotation is not an edit")

        pid = f"R{state['next_proposal']}"
        proposal = {
            "id": pid,
            "target": args.target,
            "kind": args.kind,
            "quote": args.quote,
            "annotation": args.annotation.strip(),
            "why": (args.why or "").strip(),
            "uncertain": bool(args.uncertain),
            "status": "proposed",
            "created": _now(),
            "decided": None,
            "target_sha256": block.get("sha256"),
            "locator": block.get("locator") or args.target,
        }
        state["proposals"].append(proposal)
        state["next_proposal"] += 1
        save(args.workdir, state)
    except InvalidOperation as exc:
        # state on disk is untouched; the refusal is still receipted
        receipt(args.workdir, "invalid_operation", command="propose", code=exc.code,
                detail=exc.detail, state_unchanged=True, state_hash_before=before)
        print(json.dumps({"error": exc.code, "detail": exc.detail, "state_unchanged": True},
                         ensure_ascii=False, indent=1))
        return 2
    receipt(args.workdir, "propose", proposal=pid, target=args.target, kind=args.kind)
    print(json.dumps(proposal, ensure_ascii=False, indent=1))
    return 0


# ---------------------------------------------------------------- decide

def cmd_decide(args) -> int:
    state = load(args.workdir)
    before = state["state_hash"]
    try:
        prop = next((p for p in state["proposals"] if p["id"] == args.id), None)
        if prop is None:
            raise InvalidOperation("proposal_not_found", f"no proposal {args.id!r}")
        if prop["status"] != "proposed":
            raise InvalidOperation("already_decided",
                                   f"{args.id} is already {prop['status']}; decisions are not overwritten")
        if args.accept == args.reject:
            raise InvalidOperation("ambiguous_decision", "pass exactly one of --accept/--reject")
        prop["status"] = "accepted" if args.accept else "rejected"
        prop["decided"] = {"at": _now(), "by": args.by, "note": (args.note or "").strip()}
        save(args.workdir, state)
    except InvalidOperation as exc:
        receipt(args.workdir, "invalid_operation", command="decide", code=exc.code,
                detail=exc.detail, state_unchanged=True, state_hash_before=before)
        print(json.dumps({"error": exc.code, "detail": exc.detail, "state_unchanged": True},
                         ensure_ascii=False, indent=1))
        return 2
    receipt(args.workdir, "decide", proposal=args.id, decision=prop["status"], by=args.by)
    print(json.dumps({"id": args.id, "status": prop["status"]}, ensure_ascii=False, indent=1))
    return 0


# ---------------------------------------------------------------- apply / rollback

def _next_version(state: dict) -> str:
    return f"v{len(state['versions']) + 1}"


def cmd_apply(args) -> int:
    state = load(args.workdir)
    before = state["state_hash"]
    try:
        if not state["original"]:
            raise InvalidOperation("no_source", "nothing imported; nothing to apply onto")
        wanted = [s.strip() for s in args.ids.split(",")] if args.ids else None
        candidates = [p for p in state["proposals"] if wanted is None or p["id"] in wanted]
        if wanted:
            missing = [w for w in wanted if w not in {p["id"] for p in state["proposals"]}]
            if missing:
                raise InvalidOperation("proposal_not_found", f"unknown ids {missing}")
        accepted = [p for p in candidates if p["status"] == "accepted"]
        pending = [p["id"] for p in candidates if p["status"] == "proposed"]
        if pending:
            raise InvalidOperation("proposal_not_accepted",
                                   f"still undecided: {pending}; accept or reject each one first")
        if not accepted:
            raise InvalidOperation("nothing_to_apply", "no accepted proposal in scope")
        current = state["current"]
        applied_now = [p["id"] for p in accepted]
        new_name = _next_version(state)
        state["versions"].append({
            "name": new_name,
            "note": f"applied {', '.join(applied_now)} on top of {current}",
            "paragraphs": [b["id"] for b in state["original"]],
            "applied": applied_now,
            "created": _now(),
            "from": current,
        })
        for p in accepted:
            p["status"] = "applied"
            p.setdefault("applied_in", []).append(new_name)
        state["current"] = new_name
        save(args.workdir, state)
    except InvalidOperation as exc:
        receipt(args.workdir, "invalid_operation", command="apply", code=exc.code,
                detail=exc.detail, state_unchanged=True, state_hash_before=before)
        print(json.dumps({"error": exc.code, "detail": exc.detail, "state_unchanged": True},
                         ensure_ascii=False, indent=1))
        return 2
    receipt(args.workdir, "apply", version=state["current"], proposals=applied_now)
    print(json.dumps({"version": state["current"], "applied": applied_now},
                     ensure_ascii=False, indent=1))
    return 0


def cmd_rollback(args) -> int:
    state = load(args.workdir)
    before = state["state_hash"]
    try:
        if len(state["versions"]) < 2:
            raise InvalidOperation("no_previous_version", "only the imported original exists")
        if args.to:
            names = [v["name"] for v in state["versions"]]
            if args.to not in names:
                raise InvalidOperation("version_not_found", f"{args.to!r} not in {names}")
            target = args.to
        else:
            target = state["versions"][-2]["name"]
        if target == state["current"]:
            raise InvalidOperation("already_current", f"{target} is already the current version")
        state["rollback_from"] = state["current"]
        state["current"] = target
        save(args.workdir, state)
    except InvalidOperation as exc:
        receipt(args.workdir, "invalid_operation", command="rollback", code=exc.code,
                detail=exc.detail, state_unchanged=True, state_hash_before=before)
        print(json.dumps({"error": exc.code, "detail": exc.detail, "state_unchanged": True},
                         ensure_ascii=False, indent=1))
        return 2
    receipt(args.workdir, "rollback", to=target, **{"from": state["rollback_from"]})
    print(json.dumps({"rolled_back_to": target, "from": state["rollback_from"]},
                     ensure_ascii=False, indent=1))
    return 0


def cmd_reapply(args) -> int:
    state = load(args.workdir)
    before = state["state_hash"]
    try:
        previous = state.get("rollback_from")
        if not previous:
            raise InvalidOperation("no_rollback_to_undo", "nothing was rolled back")
        state["current"] = previous
        state["reapplied_from"] = state.get("current")
        save(args.workdir, state)
    except InvalidOperation as exc:
        receipt(args.workdir, "invalid_operation", command="reapply", code=exc.code,
                detail=exc.detail, state_unchanged=True, state_hash_before=before)
        print(json.dumps({"error": exc.code, "detail": exc.detail, "state_unchanged": True},
                         ensure_ascii=False, indent=1))
        return 2
    receipt(args.workdir, "reapply", version=previous)
    print(json.dumps({"reapplied_to": previous}, ensure_ascii=False, indent=1))
    return 0


# ---------------------------------------------------------------- render / compare

def render_version(state: dict, version: str) -> list[dict]:
    """Paragraphs of a version, with annotations rendered as separate labelled blocks."""
    name = version
    entry = next((v for v in state["versions"] if v["name"] == name), None)
    if entry is None:
        raise InvalidOperation("version_not_found", f"{name!r}")
    active = []
    for p in state["proposals"]:
        if p["status"] in ("applied", "accepted") and name in (p.get("applied_in") or [name]):
            active.append(p)
    by_target: dict[str, list[dict]] = {}
    for p in active:
        by_target.setdefault(p["target"], []).append(p)
    out = []
    for block in state["original"]:
        out.append({"type": block.get("kind", "body"), "text": block.get("text", ""),
                    "id": block.get("id"), "origin": "original"})
        for p in by_target.get(block.get("id"), []):
            tag = f"[EDITORIAL ANNOTATION {p['id']} · {p['kind']}"
            if p.get("uncertain"):
                tag += " · UNCERTAIN"
            tag += "]"
            out.append({"type": "annotation",
                        "text": f"{tag}\n{p['annotation']}",
                        "id": f"{p['id']}@{block.get('id')}", "origin": "editorial"})
    return out


def cmd_diff(args) -> int:
    state = load(args.workdir)
    try:
        a = render_version(state, args.a)
        b = render_version(state, args.b)
    except InvalidOperation as exc:
        print(json.dumps({"error": exc.code, "detail": exc.detail}, ensure_ascii=False, indent=1))
        return 2
    ta = [f"[{x['id']}] {x['text']}" for x in a]
    tb = [f"[{x['id']}] {x['text']}" for x in b]
    ud = list(difflib.unified_diff(ta, tb, fromfile=args.a, tofile=args.b, lineterm="", n=1))
    changed = [x["id"] for x in b if x.get("origin") == "editorial"]
    original_ints = [x["text"] for x in b if x.get("origin") == "original"]
    original_untouched = original_ints == [x["text"] for x in a if x.get("origin") == "original"]
    out = {"a": args.a, "b": args.b, "added_blocks": changed,
           "original_text_unchanged": original_untouched,
           "unified_diff": ud, "diff_lines": len(ud)}
    if args.out:
        open(args.out, "w", encoding="utf-8").write(json.dumps(out, ensure_ascii=False, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k != "unified_diff"}, ensure_ascii=False, indent=1))
    return 0 if original_untouched else 1


def cmd_status(args) -> int:
    state = load(args.workdir)
    counts: dict[str, int] = {}
    for p in state["proposals"]:
        counts[p["status"]] = counts.get(p["status"], 0) + 1
    out = {"case_id": state.get("case_id"), "current": state["current"],
           "versions": [v["name"] for v in state["versions"]],
           "n_original_blocks": len(state["original"]),
           "proposals": counts, "state_hash": state["state_hash"]}
    if os.path.isfile(os.path.join(args.workdir, RECEIPTS)):
        with open(os.path.join(args.workdir, RECEIPTS), encoding="utf-8") as fh:
            out["n_receipts"] = sum(1 for _ in fh)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


def cmd_verify_state(args) -> int:
    state = load(args.workdir)
    recomputed = _sha({k: v for k, v in state.items() if k != "state_hash"})
    ok = recomputed == state.get("state_hash")
    print(json.dumps({"recomputed": recomputed, "stored": state.get("state_hash"), "match": ok},
                     ensure_ascii=False, indent=1))
    return 0 if ok else 1


# ---------------------------------------------------------------- export

def cmd_export(args) -> int:
    """Write the editable manuscript record and a docx spec for ooxml.py."""
    state = load(args.workdir)
    os.makedirs(args.outdir, exist_ok=True)
    version = state["current"]
    blocks = render_version(state, version)

    md_lines = [f"# Manuscript working copy — {state.get('case_id') or 'case'} ({version})", ""]
    for b in blocks:
        t = b["type"]
        if t.startswith("heading1"):
            md_lines += [f"## {b['text']}", ""]
        elif t.startswith("heading"):
            md_lines += [f"### {b['text']}", ""]
        elif t == "annotation":
            md_lines += ["> " + b["text"].replace("\n", "\n> "), ""]
        elif t == "table":
            md_lines += [b["text"], ""]
        else:
            md_lines += [b["text"], ""]
    md_path = os.path.join(args.outdir, "manuscript_current.md")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(md_lines).rstrip() + "\n")

    doc_spec = {"title": f"Working manuscript ({version})", "sections": []}
    doc_blocks = []
    for b in blocks:
        t = b["type"]
        if t.startswith("heading1"):
            doc_blocks.append({"type": "h1", "text": b["text"]})
        elif t.startswith("heading"):
            doc_blocks.append({"type": "h2", "text": b["text"]})
        elif t == "annotation":
            doc_blocks.append({"type": "p", "text": b["text"].replace("\n", " ")})
        elif t == "table":
            doc_blocks.append({"type": "p", "text": b["text"]})
        else:
            doc_blocks.append({"type": "p", "text": b["text"]})
    spec_path = os.path.join(args.outdir, "doc_spec.json")
    with open(spec_path, "w", encoding="utf-8") as fh:
        json.dump({"title": doc_spec["title"], "blocks": doc_blocks}, fh, ensure_ascii=False, indent=1)

    receipt(args.workdir, "export", version=version, markdown=os.path.abspath(md_path),
            doc_spec=os.path.abspath(spec_path), n_blocks=len(blocks))
    print(json.dumps({"version": version, "markdown": md_path, "doc_spec": spec_path,
                      "n_blocks": len(blocks)}, ensure_ascii=False, indent=1))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Offline manuscript revision workflow")
    sub = ap.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("init"); i.add_argument("--blocks", required=True); i.add_argument("--workdir", required=True)

    p = sub.add_parser("propose")
    p.add_argument("--workdir", required=True); p.add_argument("--target", required=True)
    p.add_argument("--kind", required=True); p.add_argument("--quote", required=True)
    p.add_argument("--annotation", required=True); p.add_argument("--why", default="")
    p.add_argument("--uncertain", action="store_true")

    d = sub.add_parser("decide")
    d.add_argument("--workdir", required=True); d.add_argument("--id", required=True)
    d.add_argument("--accept", action="store_true"); d.add_argument("--reject", action="store_true")
    d.add_argument("--by", required=True); d.add_argument("--note", default="")

    a = sub.add_parser("apply"); a.add_argument("--workdir", required=True); a.add_argument("--ids")
    r = sub.add_parser("rollback"); r.add_argument("--workdir", required=True); r.add_argument("--to")
    sub.add_parser("reapply").add_argument("--workdir", required=True)

    df = sub.add_parser("diff")
    df.add_argument("--workdir", required=True); df.add_argument("--a", default="v1")
    df.add_argument("--b", required=True); df.add_argument("--out")

    sub.add_parser("status").add_argument("--workdir", required=True)
    sub.add_parser("verify-state").add_argument("--workdir", required=True)
    e = sub.add_parser("export"); e.add_argument("--workdir", required=True); e.add_argument("--outdir", required=True)

    args = ap.parse_args()
    table = {"init": cmd_init, "propose": cmd_propose, "decide": cmd_decide, "apply": cmd_apply,
             "rollback": cmd_rollback, "reapply": cmd_reapply, "diff": cmd_diff,
             "status": cmd_status, "verify-state": cmd_verify_state, "export": cmd_export}
    try:
        return table[args.cmd](args)
    except InvalidOperation as exc:
        print(json.dumps({"error": exc.code, "detail": exc.detail}, ensure_ascii=False, indent=1))
        return 2


if __name__ == "__main__":
    sys.exit(main())
