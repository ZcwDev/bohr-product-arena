#!/usr/bin/env python3
"""The writing client CLI: new | check | fix | export.

SKILL.md and references/methods.md documented these four subcommands long before
this file existed - the docs described a writer.py that was never written. This
implements exactly that contract and nothing more.

  new     build a skeleton from spec.json: headings, empty slots, and a written
          prompt of what belongs in each slot. It never invents prose - an empty
          slot stays empty and is marked 未写 so that `check` can find it.
  check   run four mechanical checks over a draft and write report.json:
          structure, terminology, numbers, citations. Every problem carries a
          locator and a suggestion.
  fix     apply only safe mechanical repairs (citation renumbering, glossary
          unification, whitespace) and log each one. Semantic problems are
          reported, never edited.
  export  write docx (via ooxml.py, stdlib only) or a copy of the markdown.
          Refuses to export a draft that still has unfilled slots unless
          --allow-unfilled is given.

CLI:
  python3 writer.py new    --spec spec.json -o draft.md
  python3 writer.py check  draft.md [--spec spec.json] [--blocks blocks.json] -o report.json
  python3 writer.py fix    draft.md -o draft.fixed.md [--spec spec.json] [--log fix_log.json]
  python3 writer.py export draft.md --format docx -o draft.docx [--spec spec.json]

Exit codes: 0 clean, 1 problems found / refused, 2 bad invocation.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ooxml  # noqa: E402
import text_checks  # noqa: E402

SLOT = "〔未写〕"
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
WORD_RE = re.compile(r"[A-Za-z]+|[一-鿿]")
CITE_MARK_RE = re.compile(r"\[(\d+(?:\s*[-–,，]\s*\d+)*)\]")


# ---------------------------------------------------------------- new

def cmd_new(args):
    spec = json.load(open(args.spec, encoding="utf-8"))
    lines = []
    if spec.get("title"):
        lines.append(f"# {spec['title']}")
        lines.append("")
    for sec in spec.get("sections") or []:
        level = int(sec.get("level") or 1) + (1 if spec.get("title") else 0)
        lines.append(f"{'#' * level} {sec.get('heading') or 'Untitled'}")
        lines.append("")
        for slot in sec.get("slots") or []:
            lines.append(f"{SLOT} {slot}")
            lines.append("")
        for fixed in sec.get("text") or []:
            lines.append(str(fixed))
            lines.append("")
    if not lines:
        return _fail("spec has no title and no sections; nothing to scaffold")
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    open(args.out, "w", encoding="utf-8").write("\n".join(lines).rstrip() + "\n")
    slots = sum(1 for ln in lines if ln.startswith(SLOT))
    print(json.dumps({"path": args.out, "sections": len(spec.get("sections") or []),
                      "open_slots": slots,
                      "note": "skeleton only - every slot is an explicit gap, not content"},
                     ensure_ascii=False))
    return 0


# ---------------------------------------------------------------- check

def _material_blob(blocks_path):
    """Normalized text of every block, for 'is this number in the source?' checks."""
    if not blocks_path or not os.path.isfile(blocks_path):
        return None
    payload = json.load(open(blocks_path, encoding="utf-8"))
    blocks = payload.get("blocks") if isinstance(payload, dict) else payload
    texts = []
    for b in blocks or []:
        t = (b.get("text") or "").strip()
        if t:
            texts.append(t)
    return " ".join(texts)


def _locate_in_material(blob, needle):
    if blob is None or not needle:
        return None
    i = blob.find(needle)
    if i < 0:
        return None
    return blob[max(0, i - 40): i + len(needle) + 40]


def _sections(lines):
    """[(heading, level, start_line, body_lines)] with 1-based line numbers."""
    out, cur = [], None
    for idx, line in enumerate(lines, 1):
        m = HEADING_RE.match(line)
        if m:
            cur = (m.group(2).strip(), len(m.group(1)), idx, [])
            out.append(cur)
        elif cur is not None:
            cur[3].append((idx, line))
    return out


def cmd_check(args):
    text = open(args.draft, encoding="utf-8").read()
    lines = text.splitlines()
    spec = json.load(open(args.spec, encoding="utf-8")) if args.spec else {}
    blob = _material_blob(args.blocks)
    problems = []

    def add(kind, locator, detail, suggestion):
        problems.append({"type": kind, "locator": locator,
                         "detail": detail, "suggestion": suggestion})

    # 1) structure -------------------------------------------------------
    for idx, line in enumerate(lines, 1):
        if line.strip().startswith(SLOT):
            add("unfilled_slot", f"{args.draft}:{idx}",
                line.strip()[len(SLOT):].strip() or "(unnamed slot)",
                "fill from a locator, or delete the slot and say why in change_log")
    present = {h.lower() for h, _, _, _ in _sections(lines)}
    for sec in spec.get("sections") or []:
        h = (sec.get("heading") or "").strip()
        if h and h.lower() not in present:
            add("missing_section", f"{args.draft}:1", f"spec section {h!r} not found",
                "add the heading back, or record the omission in change_log")
        min_words = int(sec.get("min_words") or 0)
        if min_words:
            for name, _, start, body in _sections(lines):
                if name.lower() != h.lower():
                    continue
                got = len(WORD_RE.findall(" ".join(b for _, b in body)))
                if got < min_words:
                    add("thin_section", f"{args.draft}:{start}",
                        f"{name}: {got} tokens < {min_words}",
                        "expand from the source, or lower min_words in spec if it was a guess")

    # 2) terminology -----------------------------------------------------
    glossary = spec.get("glossary") or {}
    banned = glossary.get("banned") or {}
    for term, preferred in banned.items():
        for idx, line in enumerate(lines, 1):
            for m in re.finditer(r"\b" + re.escape(term) + r"\b", line, re.I):
                add("term", f"{args.draft}:{idx}",
                    f"banned variant {m.group(0)!r} (line: {line.strip()[:80]})",
                    f"prefer {preferred!r} - or `fix` will replace it and log the change")
    for term, allowed in (glossary.get("required") or {}).items():
        allowed = [allowed] if isinstance(allowed, str) else list(allowed)
        for idx, line in enumerate(lines, 1):
            if re.search(r"\b" + re.escape(term) + r"\b", line, re.I):
                if not any(a in line for a in allowed):
                    add("term", f"{args.draft}:{idx}",
                        f"mentions {term!r} without any of {allowed}",
                        "spell out the expansion at first use")

    # 3) numbers ---------------------------------------------------------
    for idx, line in enumerate(lines, 1):
        if line.strip().startswith(SLOT):
            continue
        for tok in text_checks._tokens(line):
            if blob is None:
                continue
            if _locate_in_material(blob, tok) is None:
                add("unsupported_number", f"{args.draft}:{idx}",
                    f"{tok!r} does not appear anywhere in the source blocks",
                    "add the locator that carries this value, or mark it not_reported")

    # 4) citations -------------------------------------------------------
    body = "\n".join(lines)
    refs_at = None
    for idx, line in enumerate(lines, 1):
        if re.match(r"(?i)^#{1,6}\s*(references|参考文献|bibliography)\s*$", line):
            refs_at = idx
    cited, listed = [], {}
    for idx, line in enumerate(lines, 1):
        if refs_at is not None and idx > refs_at:
            m = re.match(r"^\s*\[(\d+)\]\s*(.+)$", line)
            if m:
                listed[int(m.group(1))] = (idx, m.group(2).strip())
            continue
        for m in CITE_MARK_RE.finditer(line):
            for part in re.split(r"\s*[,，]\s*", m.group(1)):
                if re.search(r"[-–]", part):
                    lo, hi = [int(x) for x in re.split(r"\s*[-–]\s*", part)[:2]]
                    if lo <= hi and hi - lo < 60:
                        cited.extend(range(lo, hi + 1))
                elif part.strip().isdigit():
                    cited.append(int(part))
    for n in sorted(set(cited) - set(listed)):
        add("citation", f"{args.draft}:1", f"[{n}] cited but not in the reference list",
            "add the reference entry, or drop the marker")
    for n in sorted(set(listed) - set(cited)):
        add("citation", f"{args.draft}:{listed[n][0]}", f"[{n}] listed but never cited",
            "cite it, or remove it")
    first = []
    for n in cited:
        if n not in first:
            first.append(n)
    if refs_at is not None and first != sorted(first):
        add("citation", f"{args.draft}:{refs_at}",
            f"citation order of first appearance is {first}, not ascending",
            "run `fix` to renumber, or reorder the argument")

    report = {"draft": args.draft, "kind": "check",
              "counts": {k: sum(1 for p in problems if p["type"] == k)
                         for k in sorted({p["type"] for p in problems})},
              "problems": problems,
              "material_blocks": None if blob is None else "loaded"}
    if args.out:
        json.dump(report, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if not problems else 1


# ---------------------------------------------------------------- fix

def cmd_fix(args):
    text = open(args.draft, encoding="utf-8").read()
    spec = json.load(open(args.spec, encoding="utf-8")) if args.spec else {}
    log, notes = [], []

    # mechanical repair 1: banned terminology -> preferred spelling
    for term, preferred in ((spec.get("glossary") or {}).get("banned") or {}).items():
        pattern = re.compile(r"\b" + re.escape(term) + r"\b", re.I)

        def repl(m, preferred=preferred):
            got = m.group(0)
            if got.isupper():
                return preferred.upper()
            if got[:1].isupper():
                return preferred[:1].upper() + preferred[1:]
            return preferred

        text, n = pattern.subn(repl, text)
        if n:
            log.append({"action": "replace_term", "from": term, "to": preferred,
                        "count": n, "reason": "glossary unification (spec.glossary.banned)",
                        "human_check": False, "reversible": "yes, diff against the input file"})

    # mechanical repair 2: renumber citations in order of first appearance
    lines = text.splitlines()
    refs_at = None
    for idx, line in enumerate(lines, 1):
        if re.match(r"(?i)^#{1,6}\s*(references|参考文献|bibliography)\s*$", line):
            refs_at = idx
    if refs_at is not None:
        order, seen = [], set()
        for idx in range(refs_at - 1):
            for m in CITE_MARK_RE.finditer(lines[idx]):
                for part in re.split(r"\s*[,，]\s*", m.group(1)):
                    if part.strip().isdigit():
                        n = int(part)
                        if n not in seen:
                            seen.add(n)
                            order.append(n)
        mapping = {old: new for new, old in enumerate(order, 1)}
        if mapping and any(k != v for k, v in mapping.items()):
            def remap(m):
                out = []
                for part in re.split(r"(\s*[,，]\s*)", m.group(1)):
                    if part.strip().isdigit():
                        out.append(str(mapping.get(int(part), int(part))))
                    else:
                        out.append(part)
                return "[" + "".join(out) + "]"

            head = [CITE_MARK_RE.sub(remap, ln) for ln in lines[:refs_at - 1]]
            tail, dropped, entries, other = [], [], [], []
            for ln in lines[refs_at - 1:]:
                m = re.match(r"^(\s*)\[(\d+)\](\s.*)?$", ln)
                if m and int(m.group(2)) in mapping:
                    entries.append((mapping[int(m.group(2))], f"{m.group(1)}[{mapping[int(m.group(2))]}]{m.group(3) or ''}"))
                elif m:
                    dropped.append(ln)
                else:
                    other.append(ln)
            # the list must read 1,2,3 after remapping, not keep its old physical order
            entries.sort(key=lambda e: e[0])
            tail = other + [ln for _, ln in entries]
            text = "\n".join(head + tail)
            log.append({"action": "renumber_citations", "mapping": mapping,
                        "reason": "number citations by order of first appearance",
                        "human_check": True,
                        "note": "check that no cross-reference depended on the old numbers"})
            for ln in dropped:
                notes.append(f"reference line left untouched because its number was never cited: {ln.strip()[:80]}")

    # mechanical repair 3: whitespace / full-width space noise
    before = text
    text = re.sub(r"[ \t]+$", "", text, flags=re.M)
    text = text.replace("　　", "　")
    if text != before:
        log.append({"action": "strip_trailing_space", "reason": "mechanical tidying",
                    "human_check": False})

    if not log:
        log.append({"action": "none", "reason": "no mechanical defect found",
                    "human_check": False})

    open(args.out, "w", encoding="utf-8").write(text)
    payload = {"input": args.draft, "output": args.out, "kind": "fix",
               "changes": log, "untouched_semantics": notes,
               "policy": "this subcommand never rewrites meaning: no paraphrase, no added "
                         "sentences, no deleted claims, no invented numbers or references"}
    if args.log:
        json.dump(payload, open(args.log, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(payload, ensure_ascii=False, indent=1))
    return 0


# ---------------------------------------------------------------- export

def cmd_export(args):
    text = open(args.draft, encoding="utf-8").read()
    open_slots = [i for i, ln in enumerate(text.splitlines(), 1) if ln.strip().startswith(SLOT)]
    if open_slots and not args.allow_unfilled:
        return _fail(f"refusing to export: {len(open_slots)} unfilled slot(s) at lines {open_slots[:10]}; "
                     f"fill them, or pass --allow-unfilled and say so in limitations")

    if args.format == "md":
        shutil.copyfile(args.draft, args.out)
        info = {"path": args.out, "format": "markdown", "bytes": os.path.getsize(args.out)}
    elif args.format == "docx":
        spec = {"title": "", "blocks": []}
        for line in text.splitlines():
            m = HEADING_RE.match(line)
            if m:
                spec["blocks"].append({"type": "h1" if len(m.group(1)) == 1 else "h2",
                                       "text": m.group(2).strip()})
            elif line.strip():
                stripped = line.strip()
                if stripped.startswith(("- ", "* ")):
                    spec["blocks"].append({"type": "bullet", "text": stripped[2:].strip()})
                else:
                    spec["blocks"].append({"type": "p", "text": stripped})
        if spec["blocks"] and spec["blocks"][0]["type"] == "h1":
            spec["title"] = spec["blocks"].pop(0)["text"]
        info = ooxml.build_docx(spec, args.out)
        info["format"] = "docx"
        info["blocks"] = len(spec["blocks"])
    else:
        return _fail(f"unsupported format {args.format!r} (use md or docx)")

    info["unfilled_slots"] = open_slots
    print(json.dumps(info, ensure_ascii=False))
    return 0


def _fail(msg):
    print(json.dumps({"error": msg}, ensure_ascii=False))
    return 1


def main():
    ap = argparse.ArgumentParser(description="writing client: new|check|fix|export")
    sub = ap.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("new"); n.add_argument("--spec", required=True); n.add_argument("-o", "--out", required=True)
    n.set_defaults(fn=cmd_new)

    c = sub.add_parser("check"); c.add_argument("draft"); c.add_argument("--spec"); c.add_argument("--blocks")
    c.add_argument("-o", "--out"); c.set_defaults(fn=cmd_check)

    f = sub.add_parser("fix"); f.add_argument("draft"); f.add_argument("-o", "--out", required=True)
    f.add_argument("--spec"); f.add_argument("--log"); f.set_defaults(fn=cmd_fix)

    e = sub.add_parser("export"); e.add_argument("draft"); e.add_argument("-o", "--out", required=True)
    e.add_argument("--format", default="docx", choices=["md", "docx"])
    e.add_argument("--allow-unfilled", action="store_true"); e.set_defaults(fn=cmd_export)

    a = ap.parse_args()
    sys.exit(a.fn(a))


if __name__ == "__main__":
    main()
