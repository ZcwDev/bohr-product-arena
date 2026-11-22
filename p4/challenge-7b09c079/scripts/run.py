#!/usr/bin/env python3
"""weekly-skill single entry point: receive manuscript -> annotate targets -> keep revisions -> export.

    python3 run.py [--input /input] [--output /app/output] [--work /app/work] [--notes NOTES.json]

One command does everything; it never exits non-zero because of a missing optional library
or renderer (it degrades and records which path was used).  Stages:

  1 intake     case.json (+ organizer-edit-workorder.json) -> contract keys, case_id, targets
  2 index      every input file -> blocks with resolvable locators (text lines / docx para /
               pdf page+block / json path, plus char offsets)
  3 resolve    target ids -> manuscript blocks (literal id match, then ordinal fallback, flagged)
  4 propose    per target: ranked, source-quoted annotation proposals + consistency review
  5 decide     accept/reject every proposal individually (quote verification gates acceptance)
  6 apply      v1 (original bytes) -> v2 (annotations only, original text untouched),
               tracked-change DOCX (<w:ins>), unified diff, rollback + re-apply with sha256 proof
  7 faults     deliberate invalid operations, state sha unchanged
  8 render     DOCX -> PDF (soffice, else pure-Python PDF) -> PNG (pdftoppm/fitz, else stdlib)
  9 result     result.json with every output_contract key + evidence/uncertainty/notes
 10 selfcheck  selfcheck.py on the real outputs -> receipts/selfcheck.json
"""
from __future__ import annotations
# --- evaluator-environment bootstrap (injected) ---
# The replay image keeps the science stack (fitz / matplotlib / python-docx /
# python-pptx / playwright / CairoSVG) inside its own venv and points at it with
# WEEKLY_TOOLS_PYTHON; a bare `python3` is miniconda's and may not have them, which
# is how a run ends up with no rendered previews and no PDF parsing. Re-exec under
# that interpreter when it is present, and stay put when it is not.
import os as _os, sys as _sys
_TOOLS_PY = _os.environ.get("WEEKLY_TOOLS_PYTHON") or "/opt/weekly-tools/bin/python"
if (_os.path.exists(_TOOLS_PY) and not _os.environ.get("_SKILL_REEXEC")
        and _os.path.realpath(_sys.executable) != _os.path.realpath(_TOOLS_PY)):
    _os.environ["_SKILL_REEXEC"] = "1"
    try:
        _os.execv(_TOOLS_PY, [_TOOLS_PY, _os.path.abspath(__file__)] + _sys.argv[1:])
    except OSError:
        pass
# --- end evaluator-environment bootstrap ---

import argparse
import copy
import csv
import difflib
import hashlib
import json
import platform
import re
import shutil
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import wk_annotate as ann  # noqa: E402
import wk_docx  # noqa: E402
import wk_io  # noqa: E402
import wk_render  # noqa: E402

VERSION = "2.0.0"
EDITABLE_TEXT = {".md", ".markdown", ".txt", ".text", ".tex", ".rst"}
READABLE = EDITABLE_TEXT | {".docx", ".pdf", ".json", ".bib"}
SKIP_NAMES = {"task.md", "readme.md", "contract.md", "output-contract.md", "rubric.md"}
NR = "not_reported"


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fsha(p: Path) -> str:
    return sha(p.read_bytes())


def wjson(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def cjk_ratio(s: str) -> float:
    if not s:
        return 0.0
    return len(re.findall(r"[\u4e00-\u9fff]", s)) / max(1, len(re.findall(r"\S", s)))


class Run:
    def __init__(self, args):
        self.inp = Path(args.input).resolve()
        self.out = Path(args.output).resolve()
        self.work = Path(args.work).resolve() if args.work else (self.out.parent / "work").resolve()
        self.notes_file = args.notes
        self.t0 = time.time()
        self.log: list[dict] = []
        self.uncertainty: list[dict] = []
        self.notes: list[str] = []
        self.blocks: list[dict] = []
        self.files: list[dict] = []
        self.case: dict = {}
        self.case_rel = None
        self.workorder: dict = {}
        self.workorder_rel = None
        self.targets: list[str] = []
        self.resolved: dict = {}
        self.proposals: list[dict] = []
        self.decisions: list[dict] = []
        self.versions: list[dict] = []
        self.faults: list[dict] = []
        self.renders: list[dict] = []
        self.artifacts: list[dict] = []
        self.reviews: dict = {}
        self.status = "partial"
        self.status_reason = ""
        self.env = {}

    # ------------------------------------------------------------------ util
    def step(self, name: str, **kw):
        rec = {"step": name, "t": round(time.time() - self.t0, 2), **kw}
        self.log.append(rec)
        print(json.dumps(rec, ensure_ascii=False), flush=True)

    def unc(self, code: str, detail: str, **kw):
        rec = {"code": code, "detail": detail, **kw}
        if rec not in self.uncertainty:
            self.uncertainty.append(rec)

    def rel_out(self, p: Path) -> str:
        return str(p.resolve().relative_to(self.out))

    def add_artifact(self, p: Path, role: str, **kw):
        if p.is_file():
            self.artifacts.append({"path": self.rel_out(p), "role": role, "bytes": p.stat().st_size,
                                   "sha256": fsha(p), **kw})

    # ------------------------------------------------------------------ 1 intake
    def intake(self):
        self.env = wk_render.probe()
        self.env.update({"python": sys.executable, "python_version": platform.python_version(),
                         "pdftotext": wk_render.which("pdftotext") or "missing",
                         "weekly_tools_python": _TOOLS_PY if _os.path.exists(_TOOLS_PY) else "missing"})
        cand = self.inp / "case.json"
        if not cand.is_file():
            for p in sorted(self.inp.rglob("*.json")):
                try:
                    obj = json.loads(wk_io.read_text_file(p))
                except Exception:
                    continue
                if isinstance(obj, dict) and "case_id" in obj:
                    cand = p
                    self.notes.append(f"/input/case.json not found; used {p.relative_to(self.inp)} (first JSON with a case_id key).")
                    break
        if cand.is_file():
            try:
                self.case = json.loads(wk_io.read_text_file(cand))
                self.case_rel = str(cand.relative_to(self.inp))
            except Exception as exc:  # noqa: BLE001
                self.unc("case_json_unparseable", f"{cand}: {exc}")
        if not self.case:
            self.unc("case_json_missing", f"No case.json (or JSON with case_id) under {self.inp}")
        wo = self.case.get("organizer_edit_workorder") if isinstance(self.case, dict) else None
        if isinstance(wo, dict):
            self.workorder, self.workorder_rel = wo, f"{self.case_rel}#$.organizer_edit_workorder"
        for p in sorted(self.inp.rglob("*workorder*.json")):
            try:
                obj = json.loads(wk_io.read_text_file(p))
                if isinstance(obj, dict):
                    if not self.workorder:
                        self.workorder = obj
                    self.workorder_rel = str(p.relative_to(self.inp))
                    if wo and obj.get("target_ids") and obj.get("target_ids") != wo.get("target_ids"):
                        self.unc("workorder_conflict", "target_ids differ between case.json and workorder file; "
                                 "the standalone workorder file is used.",
                                 case_targets=wo.get("target_ids"), file_targets=obj.get("target_ids"))
                        self.workorder = obj
                    break
            except Exception:
                continue
        t = self.workorder.get("target_ids") or self.case.get("target_ids") or []
        self.targets = [str(x) for x in t] if isinstance(t, list) else [str(t)]
        if not self.targets:
            self.unc("no_target_ids", "Neither the case nor a workorder lists target_ids; no edit is authorized.")
        self.step("intake", case_file=self.case_rel, case_id=self.case.get("case_id", NR),
                  workorder=self.workorder_rel, targets=self.targets, env=self.env)

    # ------------------------------------------------------------------ 2 index
    def index(self):
        for p in sorted(self.inp.rglob("*")):
            if not p.is_file() or any(part.startswith(".") for part in p.relative_to(self.inp).parts):
                continue
            rel = str(p.relative_to(self.inp))
            suf = p.suffix.lower()
            rec = {"file": rel, "bytes": p.stat().st_size, "sha256": fsha(p), "suffix": suf, "status": "indexed"}
            self.files.append(rec)
            if rel == self.case_rel or (self.workorder_rel and rel == self.workorder_rel):
                rec["role"] = "task"
                rec["status"] = "task_metadata"
                continue
            if suf not in READABLE:
                rec["status"] = "not_parsed (unsupported type)"
                continue
            if p.stat().st_size > 80 * 1024 * 1024:
                rec["status"] = "not_parsed (larger than 80 MB)"
                self.unc("file_too_large", rel)
                continue
            role = "task" if p.name.lower() in SKIP_NAMES else ("source" if suf in (".pdf", ".bib") else "material")
            rec["role"] = role
            try:
                n0 = len(self.blocks)
                if suf in EDITABLE_TEXT or suf == ".bib":
                    for b in wk_io.text_blocks(wk_io.read_text_file(p)):
                        b["loc"] = {"file": rel, "kind": "text", "line_start": b["line_start"], "line_end": b["line_end"]}
                        b["role"] = role
                        self.blocks.append(b)
                    if suf == ".bib":
                        for b in self.blocks[n0:]:
                            b["is_reference_entry"] = True
                elif suf == ".docx":
                    for b in wk_io.docx_paragraphs(p):
                        if not b["text"].strip():
                            continue
                        b["loc"] = {"file": rel, "kind": "docx", "para": b["para"]}
                        b["role"] = role
                        self.blocks.append(b)
                    rec["docx_parts"] = wk_io.docx_footnotes_headers(p)
                elif suf == ".pdf":
                    pages, ext = wk_io.pdf_pages(p)
                    rec["extractor"], rec["pages"] = ext, len(pages)
                    for pi, page in enumerate(pages, 1):
                        for bi, blk in enumerate(page, 1):
                            self.blocks.append({"text": blk["text"], "role": role, "is_heading": False,
                                                "is_table": False, "is_equation": False,
                                                "loc": {"file": rel, "kind": "pdf", "page": pi, "block": bi,
                                                        "extractor": ext}})
                elif suf == ".json":
                    obj = json.loads(wk_io.read_text_file(p))
                    for b in wk_io.json_blocks(obj):
                        self.blocks.append({"text": b["text"], "id": b["id"], "role": role,
                                            "is_heading": False, "is_table": False, "is_equation": False,
                                            "section": b.get("section", ""),
                                            "loc": {"file": rel, "kind": "json", "json_path": b["json_path"]}})
                rec["blocks"] = len(self.blocks) - n0
                if rec["blocks"] == 0:
                    rec["status"] = "no_text"
                    self.unc("no_text_extracted", f"{rel}: no text blocks (scanned/empty?)")
            except wk_io.ReadError as exc:
                rec["status"] = f"read_error:{exc.code}"
                self.unc(exc.code, exc.detail, file=rel)
            except Exception as exc:  # noqa: BLE001
                rec["status"] = f"read_error:{type(exc).__name__}"
                self.unc("reader_exception", f"{rel}: {exc!r}", file=rel)
        ann.mark_reference_blocks(self.blocks)
        self.step("index", files=len(self.files), blocks=len(self.blocks),
                  by_file={f["file"]: f.get("blocks", f["status"]) for f in self.files})

    # ------------------------------------------------------------------ 3 resolve
    def resolve_targets(self):
        id_hits = {}
        for t in self.targets:
            pat = re.compile(r"(?<![\w-])" + re.escape(t) + r"(?![\w])")
            for i, b in enumerate(self.blocks):
                if b.get("id") == t:
                    id_hits[t] = (i, "json_id")
                    break
                if b["role"] != "task" and pat.search(b["text"]):
                    rest = pat.sub("", b["text"])
                    rest = re.sub(r"[#<>!\-\[\]{}():*_`|=\s]|anchor|id", "", rest)
                    if len(rest) < 15 or b.get("is_heading"):
                        j = i + 1
                        while j < len(self.blocks) and self.blocks[j]["loc"]["file"] == b["loc"]["file"] and (
                                self.blocks[j].get("is_heading") or not self.blocks[j]["text"].strip()):
                            j += 1
                        if j < len(self.blocks) and self.blocks[j]["loc"]["file"] == b["loc"]["file"]:
                            id_hits[t] = (j, "id_marker_before_block")
                            break
                    else:
                        id_hits[t] = (i, "id_inside_block")
                        break
        ms_files = []
        for t, (i, _) in id_hits.items():
            f = self.blocks[i]["loc"]["file"]
            if f not in ms_files:
                ms_files.append(f)
        if not ms_files:  # choose editable manuscript files
            editable = [f for f in self.files if f.get("role") == "material" and f.get("blocks")]
            editable.sort(key=lambda f: (f["suffix"] not in EDITABLE_TEXT | {".docx"}, f["file"]))
            ms_files = [f["file"] for f in editable if f["suffix"] in EDITABLE_TEXT | {".docx", ".json"}]
            if not ms_files:
                ms_files = [f["file"] for f in editable] or [f["file"] for f in self.files
                                                          if f.get("role") == "source" and f.get("blocks")][:1]
        self.ms_files = ms_files
        for b in self.blocks:
            if b["loc"]["file"] in ms_files:
                b["role"] = "manuscript"
        body = [i for i, b in enumerate(self.blocks) if b["role"] == "manuscript" and not b.get("is_heading")
                and not b.get("is_table") and not b.get("is_equation") and not b.get("is_reference_entry")
                and len(b["text"].strip()) >= 30]
        for t in self.targets:
            if t in id_hits:
                i, how = id_hits[t]
                self.resolved[t] = {"block_index": i, "method": how, "confidence": "high"}
                continue
            m = re.search(r"P(\d+)\s*$", t)
            if m and body and 1 <= int(m.group(1)) <= len(body):
                i = body[int(m.group(1)) - 1]
                self.resolved[t] = {"block_index": i, "method": f"ordinal: body paragraph #{int(m.group(1))} "
                                    f"of {len(body)} in {', '.join(ms_files)}", "confidence": "low"}
                self.unc("target_resolved_by_ordinal", f"{t} does not occur literally in the inputs; mapped to body "
                         f"paragraph #{int(m.group(1))} ({wk_io.locator_str(self.blocks[i]['loc'])}). "
                         "Human confirmation of the target paragraph is required.", target_id=t)
            else:
                self.resolved[t] = None
                self.unc("target_not_found", f"{t}: no block with this id and no manuscript paragraph to map it to "
                         f"(manuscript files: {ms_files or 'none'}).", target_id=t)
        self.step("resolve", manuscript_files=ms_files,
                  resolved={t: (wk_io.locator_str(self.blocks[r['block_index']]['loc']) + f" via {r['method']}")
                            if r else NR for t, r in self.resolved.items()})

    # ------------------------------------------------------------------ 4/5 propose + decide
    def _verify_quote(self, q: dict) -> bool:
        try:
            region = wk_io.resolve_locator(self.inp, q["locator"])
        except Exception:
            return False
        loc = q["locator"]
        if "char_start" in loc:
            region = region[loc["char_start"]:loc["char_end"]]
        return wk_io.quote_in(region, q["quote"])

    def _find_quote_anywhere(self, quote: str):
        nq = wk_io.norm_cmp(quote)
        for b in self.blocks:
            if b["role"] == "task":
                continue
            raw = b["text"]
            if nq and nq in wk_io.norm_cmp(raw):
                # locate exact char span when possible
                k = raw.find(quote)
                loc = dict(b["loc"])
                if k >= 0:
                    loc["char_start"], loc["char_end"] = k, k + len(quote)
                return {"quote": quote, "locator": loc, "loc_str": wk_io.locator_str(loc)}
        return None

    def propose_and_decide(self):
        cands = ann.sentence_candidates([b if b["role"] != "task" else {**b, "is_heading": True} for b in self.blocks])
        agent_notes = {}
        if self.notes_file:
            try:
                agent_notes = json.loads(Path(self.notes_file).read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                self.unc("agent_notes_unreadable", f"{self.notes_file}: {exc!r}")
        for t in self.targets:
            r = self.resolved.get(t)
            if not r:
                self.decisions.append({"change_id": f"{t}#none", "target_id": t, "decision": "not_applied",
                                       "reason": "target block not found in the supplied inputs"})
                continue
            tb = self.blocks[r["block_index"]]
            lang = "zh" if cjk_ratio(tb["text"]) > 0.3 else "en"
            review = ann.review_target(r, self.blocks)
            self.reviews[t] = review
            props = ann.propose_for_target(r, self.blocks, cands, lang=lang, k=2)
            plist = []
            an = agent_notes.get(t) if isinstance(agent_notes, dict) else None
            if an:
                text = an.get("text") if isinstance(an, dict) else str(an)
                quotes = re.findall(r"[\u201c\"]([^\u201d\"]{12,})[\u201d\"]", text or "")
                found = [self._find_quote_anywhere(q) for q in quotes]
                p = {"change_id": f"{t}#agent", "target_id": t, "origin": "agent_notes_file",
                     "anchor": props[0]["anchor"] if props else {"quote": wk_io.norm_ws(tb['text'][:200]),
                                                                 "locator": dict(tb["loc"])},
                     "quotes": [f for f in found if f], "annotation": text or ""}
                p["verification"] = {"quotes_in_note": len(quotes), "quotes_found_in_inputs": sum(1 for f in found if f)}
                p["valid"] = bool(quotes) and all(found)
                plist.append(p)
            for pr in props:
                for q in pr["quotes"]:
                    q["loc_str"] = wk_io.locator_str(q["locator"])
                pr["anchor"]["loc_str"] = wk_io.locator_str(pr["anchor"]["locator"])
                p = {"change_id": f"{t}#r{pr['rank']}", "target_id": t, "origin": "deterministic_ranker",
                     "rank": pr["rank"], "score": pr["score"], "anchor": pr["anchor"], "quotes": pr["quotes"],
                     "annotation": ann.compose_annotation(t, pr, review, lang)}
                p["verification"] = {"quotes_verified": [self._verify_quote(q) for q in pr["quotes"]],
                                     "anchor_verified": self._verify_quote(pr["anchor"])}
                p["valid"] = all(p["verification"]["quotes_verified"]) and p["verification"]["anchor_verified"]
                plist.append(p)
            self.proposals.extend(plist)
            accepted = None
            for p in plist:
                if accepted is None and p["valid"]:
                    accepted = p
                    reason = ("agent-authored note; every quoted span was found verbatim in the inputs"
                              if p["origin"] == "agent_notes_file" else
                              "highest-ranked proposal whose quotes and anchor re-resolve verbatim to the input files")
                    self.decisions.append({"change_id": p["change_id"], "target_id": t, "decision": "accept",
                                           "reason": reason})
                else:
                    if not p["valid"]:
                        reason = "rejected: verification failed " + json.dumps(p["verification"], ensure_ascii=False)
                    else:
                        reason = (f"rejected: alternative to accepted {accepted['change_id']}; workorder asks for one "
                                  "annotation per target")
                    self.decisions.append({"change_id": p["change_id"], "target_id": t, "decision": "reject",
                                           "reason": reason})
            if accepted is None:
                self.decisions.append({"change_id": f"{t}#none", "target_id": t, "decision": "not_applied",
                                       "reason": "no proposal with verifiable source quotes (no related material?)"})
                self.unc("no_verified_proposal", f"{t}: no annotation could be grounded in the supplied material",
                         target_id=t)
            else:
                accepted["accepted"] = True
        self.step("propose_decide", proposals=len(self.proposals),
                  accepted=[d["change_id"] for d in self.decisions if d["decision"] == "accept"])

    # ------------------------------------------------------------------ 6 apply
    def accepted(self) -> list[dict]:
        return [p for p in self.proposals if p.get("accepted")]

    @staticmethod
    def _text_insert(raw: str, line_end: int, note: str, suffix: str) -> tuple[int, str]:
        if suffix == ".tex":
            esc = re.sub(r"([&%$#_{}])", r"\\\1", note).replace("~", r"\textasciitilde{}").replace("^", r"\^{}")
            frag = "\n\n\\noindent\\textit{" + esc + "}"
        else:
            frag = "\n\n> " + note.replace("\n", " ")
        pos, line = 0, 1
        while line <= line_end:
            k = raw.find("\n", pos)
            if k < 0:
                pos = len(raw)
                break
            pos = k + 1 if line < line_end else k
            line += 1
        if pos > 0 and raw[pos - 1:pos] == "\r":  # CRLF: insert before \r
            pos -= 1
        return pos, frag

    def apply_text(self, ms: str, raw: str, props: list[dict], suffix: str) -> tuple[str, list]:
        ins = []
        for p in props:
            b = self.blocks[self.resolved[p["target_id"]]["block_index"]]
            pos, frag = self._text_insert(raw, b["loc"]["line_end"], p["annotation"], suffix)
            ins.append((pos, frag, p["change_id"]))
        new = raw
        for pos, frag, _ in sorted(ins, key=lambda x: -x[0]):
            new = new[:pos] + frag + new[pos:]
        return new, ins

    @staticmethod
    def revert_text(v2: str, ins: list, drop: set | None = None) -> str:
        # remove inserted fragments (positions refer to the original text; walk forward with shift)
        out, shift = v2, 0
        for pos, frag, cid in sorted(ins, key=lambda x: x[0]):
            at = pos + shift
            if drop is not None and cid not in drop:
                shift += len(frag)
                continue
            if out[at:at + len(frag)] != frag:
                raise ValueError(f"rollback anchor mismatch for {cid}")
            out = out[:at] + out[at + len(frag):]
        return out

    def layout_blocks(self, ms_blocks: list[int], props_by_block: dict) -> list[dict]:
        lb = []
        first = True
        for i in ms_blocks:
            b = self.blocks[i]
            t = b["text"]
            s = t.lstrip()
            role = "body"
            m = re.match(r"(#{1,6})\s+(.*)", s, re.S)
            if m:
                lvl = len(m.group(1))
                role = "title" if (lvl == 1 and first) else {1: "h1", 2: "h2"}.get(lvl, "h3")
                t = m.group(2)
                first = False
            elif b.get("is_heading"):
                role = "h2"
            elif b.get("is_table"):
                role = "table"
            elif b.get("is_equation"):
                role = "equation"
            elif b.get("is_reference_entry"):
                role = "ref"
            elif re.match(r"(\*\*)?(Table|Fig\.?|Figure|表|图)\s*\d", s):
                role = "caption"
            lb.append({"text": t, "role": role})
            for p in props_by_block.get(i, []):
                lb.append({"text": p["annotation"], "role": "annotation"})
        return lb

    def apply(self):
        mdir = self.out / "manuscript"
        ddir = self.out / "diff"
        mdir.mkdir(parents=True, exist_ok=True)
        ddir.mkdir(parents=True, exist_ok=True)
        acc = self.accepted()
        for ms in self.ms_files:
            src = self.inp / ms
            suf = src.suffix.lower()
            stem = re.sub(r"[^\w\-.]+", "_", Path(ms).stem)
            ms_idx = [i for i, b in enumerate(self.blocks) if b["loc"]["file"] == ms]
            props = [p for p in acc if self.blocks[self.resolved[p["target_id"]]["block_index"]]["loc"]["file"] == ms]
            pbb = {}
            for p in props:
                pbb.setdefault(self.resolved[p["target_id"]]["block_index"], []).append(p)
            ver = {"manuscript": ms, "kind": suf, "accepted_changes": [p["change_id"] for p in props]}
            header = (self.case.get("source", {}) or {}).get("title") or self.case.get("case_id") or ms
            footer = f"{self.case.get('case_id', NR)} | weekly-skill v{VERSION} | editorial annotations are tracked insertions"
            try:
                if suf in EDITABLE_TEXT:
                    raw = wk_io.read_text_file(src)
                    v1 = mdir / f"{stem}_v1{suf}"
                    shutil.copyfile(src, v1)
                    v2s, ins = self.apply_text(ms, raw, props, suf)
                    v2 = mdir / f"{stem}_v2{suf}"
                    v2.write_text(v2s, encoding="utf-8")
                    rb = self.revert_text(v2s, ins)
                    rbp = mdir / f"{stem}_rolled_back{suf}"
                    rbp.write_text(rb, encoding="utf-8")
                    re2, _ = self.apply_text(ms, rb, props, suf)
                    rap = mdir / f"{stem}_reapplied{suf}"
                    rap.write_text(re2, encoding="utf-8")
                    ver.update(self._version_proof(raw, v2s, rb, re2, v1, v2, rbp, rap))
                    # partial rollback: reject the first accepted change after the fact
                    if len(props) > 1:
                        one = self.revert_text(v2s, ins, drop={props[0]["change_id"]})
                        pr = mdir / f"{stem}_without_{re.sub(r'[^A-Za-z0-9]+', '_', props[0]['change_id'])}{suf}"
                        pr.write_text(one, encoding="utf-8")
                        ver["single_change_rollback"] = {"change_id": props[0]["change_id"], "file": self.rel_out(pr),
                                                         "other_changes_still_present": all(
                                                             p["annotation"].replace("\n", " ")[:60] in one
                                                             for p in props[1:])}
                        self.add_artifact(pr, "single-change rollback")
                    self._diff(raw, v2s, ddir / f"{stem}.diff", ms)
                    docx_blocks = [dict(self.blocks[i]) for i in ms_idx]
                    for p in props:
                        p["_bi"] = ms_idx.index(self.resolved[p["target_id"]]["block_index"])
                    d1 = mdir / f"{stem}_v1.docx"
                    wk_docx.build_docx(docx_blocks, d1, header, footer.replace("tracked insertions", "original"))
                    d2 = mdir / f"{stem}_v2_tracked.docx"
                    inserts = {}
                    for p in props:
                        inserts.setdefault(p["_bi"], []).append((p["annotation"], p["change_id"]))
                    r = wk_docx.build_docx(docx_blocks, d2, header, footer, inserts_after=inserts)
                    ver["docx_tracked_insertions"] = r["tracked_insertions"]
                    for a, role in ((v1, "v1 original (byte copy)"), (v2, "v2 annotated (editable source format)"),
                                    (rbp, "rollback of v2"), (rap, "re-application"), (d1, "v1 DOCX export"),
                                    (d2, "v2 DOCX with Word tracked insertions (accept/reject per change)")):
                        self.add_artifact(a, role)
                    self.add_artifact(ddir / f"{stem}.diff", "unified diff v1->v2")
                    self._render(d2, self.layout_blocks(ms_idx, pbb), f"{stem}_v2", header, footer, props)
                elif suf == ".docx":
                    v1 = mdir / f"{stem}_v1.docx"
                    shutil.copyfile(src, v1)
                    v2 = mdir / f"{stem}_v2_tracked.docx"
                    ins = {}
                    for p in props:
                        ins.setdefault(self.blocks[self.resolved[p["target_id"]]["block_index"]]["loc"]["para"], []
                                       ).append((p["annotation"], p["change_id"]))
                    r = wk_docx.patch_docx(src, v2, ins)
                    ver["docx_tracked_insertions"] = r["tracked_insertions"]
                    rbp = mdir / f"{stem}_rolled_back.docx"
                    self._docx_revert(v2, rbp)
                    rap = mdir / f"{stem}_reapplied.docx"
                    wk_docx.patch_docx(rbp, rap, ins)
                    dx = lambda p: __import__("zipfile").ZipFile(p).read("word/document.xml")  # noqa: E731
                    ver.update({"v1": self.rel_out(v1), "v2": self.rel_out(v2), "rolled_back": self.rel_out(rbp),
                                "reapplied": self.rel_out(rap),
                                "rollback_equals_v1": sha(dx(rbp)) == sha(dx(v1)),
                                "reapply_equals_v2_text": [x["text"] for x in wk_io.docx_paragraphs(rap)] ==
                                [x["text"] for x in wk_io.docx_paragraphs(v2)],
                                "compare": "sha256 of word/document.xml (other zip parts copied byte-for-byte)",
                                "original_parts_preserved": self._docx_parts_same(src, v2)})
                    t1 = "\n\n".join(x["text"] for x in wk_io.docx_paragraphs(v1))
                    t2 = "\n\n".join(x["text"] for x in wk_io.docx_paragraphs(v2))
                    ver["only_insertions"] = self._diff(t1, t2, ddir / f"{stem}.diff", ms)
                    for a, role in ((v1, "v1 original (byte copy)"),
                                    (v2, "v2 DOCX with Word tracked insertions (accept/reject per change)"),
                                    (rbp, "rollback of v2"), (rap, "re-application")):
                        self.add_artifact(a, role)
                    self.add_artifact(ddir / f"{stem}.diff", "unified diff (paragraph text) v1->v2")
                    self._render(v2, self.layout_blocks(ms_idx, pbb), f"{stem}_v2", header, footer, props)
                else:  # json / pdf manuscripts: export an editable markdown + docx from the blocks
                    lines1, lines2 = [], []
                    for i in ms_idx:
                        b = self.blocks[i]
                        lab = f"<!-- {b.get('id') or wk_io.locator_str(b['loc'])} -->"
                        lines1 += [lab, b["text"], ""]
                        lines2 += [lab, b["text"], ""]
                        for p in pbb.get(i, []):
                            lines2 += ["> " + p["annotation"], ""]
                    v1 = mdir / f"{stem}_v1_extracted.md"
                    v2 = mdir / f"{stem}_v2_extracted.md"
                    v1.write_text("\n".join(lines1), encoding="utf-8")
                    v2.write_text("\n".join(lines2), encoding="utf-8")
                    self.unc("manuscript_not_natively_editable",
                             f"{ms} is {suf}; an editable Markdown/DOCX was extracted from its text blocks "
                             "(each block labelled with its locator). Layout of the original is not preserved.")
                    if suf == ".json":
                        obj = json.loads(wk_io.read_text_file(src))
                        o2 = copy.deepcopy(obj)
                        for p in props:
                            jp = self.blocks[self.resolved[p["target_id"]]["block_index"]]["loc"]["json_path"]
                            parent = wk_io.json_get(o2, jp.rsplit(".", 1)[0])
                            parent["editorial_annotation"] = {"text": p["annotation"], "change_id": p["change_id"],
                                                              "status": "accepted", "author": wk_docx.AUTHOR}
                        j2 = mdir / f"{stem}_v2.json"
                        wjson(j2, o2)
                        o3 = copy.deepcopy(o2)
                        for p in props:
                            jp = self.blocks[self.resolved[p["target_id"]]["block_index"]]["loc"]["json_path"]
                            wk_io.json_get(o3, jp.rsplit(".", 1)[0]).pop("editorial_annotation", None)
                        rbp = mdir / f"{stem}_rolled_back.json"
                        wjson(rbp, o3)
                        ver.update({"v2_json": self.rel_out(j2), "rolled_back": self.rel_out(rbp),
                                    "rollback_equals_v1": o3 == obj,
                                    "compare": "parsed JSON equality with the original"})
                        self.add_artifact(j2, "v2 JSON (annotation added as sibling key)")
                        self.add_artifact(rbp, "rollback of v2 JSON")
                    ver["only_insertions"] = self._diff("\n".join(lines1), "\n".join(lines2), ddir / f"{stem}.diff", ms)
                    d2 = mdir / f"{stem}_v2_tracked.docx"
                    bl = [dict(self.blocks[i]) for i in ms_idx]
                    inserts = {}
                    for p in props:
                        inserts.setdefault(ms_idx.index(self.resolved[p["target_id"]]["block_index"]), []
                                           ).append((p["annotation"], p["change_id"]))
                    r = wk_docx.build_docx(bl, d2, header, footer, inserts_after=inserts)
                    ver["docx_tracked_insertions"] = r["tracked_insertions"]
                    ver.update({"v1": self.rel_out(v1), "v2": self.rel_out(v2)})
                    for a, role in ((v1, "v1 extracted text"), (v2, "v2 extracted text + annotations"),
                                    (d2, "v2 DOCX with Word tracked insertions")):
                        self.add_artifact(a, role)
                    self.add_artifact(ddir / f"{stem}.diff", "unified diff v1->v2")
                    self._render(d2, self.layout_blocks(ms_idx, pbb), f"{stem}_v2", header, footer, props)
            except Exception as exc:  # noqa: BLE001
                ver["error"] = f"{type(exc).__name__}: {exc}"
                ver["traceback"] = traceback.format_exc()[-1500:]
                self.unc("apply_failed", f"{ms}: {exc!r}")
            self.versions.append(ver)
        self.step("apply", versions=[{k: v for k, v in x.items() if k != "traceback"} for x in self.versions])

    def _version_proof(self, raw, v2s, rb, re2, v1, v2, rbp, rap) -> dict:
        return {"v1": self.rel_out(v1), "v2": self.rel_out(v2), "rolled_back": self.rel_out(rbp),
                "reapplied": self.rel_out(rap), "sha256_v1": fsha(v1), "sha256_v2": fsha(v2),
                "sha256_rolled_back": fsha(rbp), "sha256_reapplied": fsha(rap),
                "rollback_equals_v1": rb == raw, "reapply_equals_v2": re2 == v2s,
                "only_insertions": all(not l.startswith("-") or l.startswith("---")
                                       for l in difflib.unified_diff(raw.splitlines(), v2s.splitlines(), lineterm="")),
                "original_blocks_preserved_verbatim": all(
                    b["text"] in v2s for b in self.blocks if b["role"] == "manuscript" and b["text"] in raw)}

    def _diff(self, a: str, b: str, path: Path, ms: str) -> bool:
        lines = list(difflib.unified_diff(a.splitlines(), b.splitlines(), f"{ms} (v1)", f"{ms} (v2)", lineterm=""))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return all(not l.startswith("-") or l.startswith("---") for l in lines)

    @staticmethod
    def _docx_revert(src: Path, dst: Path):
        import zipfile
        with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
            for it in zin.infolist():
                data = zin.read(it.filename)
                if it.filename == "word/document.xml":
                    x = data.decode("utf-8")
                    x = re.sub(r'<w:p><w:pPr><w:pStyle w:val="EditorialAnnotation"/>.*?</w:p>', "", x, flags=re.S)
                    data = x.encode("utf-8")
                zout.writestr(it, data)

    @staticmethod
    def _docx_parts_same(a: Path, b: Path) -> dict:
        import zipfile
        za, zb = zipfile.ZipFile(a), zipfile.ZipFile(b)
        res = {}
        for n in za.namelist():
            if n == "word/document.xml":
                continue
            res[n] = n in zb.namelist() and za.read(n) == zb.read(n)
        return {"all_other_parts_identical": all(res.values()), "parts_checked": len(res)}

    # ------------------------------------------------------------------ 7 faults
    def fault_injection(self):
        acc = self.accepted()
        v = next((x for x in self.versions if x.get("v2") and not x.get("error")), None)
        if not v:
            sr = self.out / "records" / "status_report.md"
            if not sr.is_file():
                self.notes.append("Fault injection skipped: no state file exists to protect.")
                return
            v = {"v2": self.rel_out(sr)}
        state = self.out / v["v2"]
        allowed = set(self.targets)
        # 0: annotate an authorized target whose text is not present in the inputs
        for t in self.targets:
            if not self.resolved.get(t):
                def ann_missing(t=t):
                    if not self.resolved.get(t):
                        raise KeyError(f"target {t} has no located paragraph in the inputs; refusing to invent "
                                       "an anchor")
                before = fsha(state)
                try:
                    ann_missing()
                    outcome, err = "NOT_REFUSED (bug)", None
                except KeyError as exc:
                    outcome, err = "refused", f"KeyError: {exc}"
                self.faults.append({"operation": f"annotate unlocated target {t}",
                                    "label": "deliberate fault injection (local CLI), not a platform failure",
                                    "outcome": outcome, "error": err, "state_file": v["v2"],
                                    "sha256_before": before, "sha256_after": fsha(state),
                                    "state_unchanged": before == fsha(state)})

        def attempt(name, fn):
            before = fsha(state)
            try:
                fn()
                outcome, err = "NOT_REFUSED (bug)", None
            except (ValueError, KeyError, IndexError, PermissionError) as exc:
                outcome, err = "refused", f"{type(exc).__name__}: {exc}"
            after = fsha(state)
            self.faults.append({"operation": name, "label": "deliberate fault injection (local CLI), not a platform failure",
                                "outcome": outcome, "error": err, "state_file": v["v2"],
                                "sha256_before": before, "sha256_after": after, "state_unchanged": before == after})

        def guarded_apply(target_id, prop):
            if target_id not in allowed:
                raise ValueError(f"target {target_id!r} is not authorized by the workorder {sorted(allowed)}")
            for q in prop["quotes"]:
                if not self._verify_quote(q):
                    raise ValueError(f"quote does not occur at {q.get('loc_str')}: {q['quote'][:60]!r}")
            raise IndexError("unreachable in fault test")

        # 1: annotate a real, non-target manuscript paragraph (or an id the workorder never listed)
        if not any(b["role"] == "manuscript" for b in self.blocks):
            bogus = (self.targets[0] + "-UNLISTED") if self.targets else "UNLISTED"
            attempt(f"annotate id not listed in workorder ({bogus})", lambda: guarded_apply(bogus, {"quotes": []}))
        non_target = next((b for i, b in enumerate(self.blocks) if b["role"] == "manuscript"
                           and not b.get("is_heading") and i not in {r["block_index"] for r in self.resolved.values() if r}), None)
        if non_target is not None:
            pseudo = non_target.get("id") or wk_io.locator_str(non_target["loc"])
            attempt(f"annotate unauthorized block {pseudo}", lambda: guarded_apply(pseudo, {"quotes": []}))
        # 2: altered quote (one character changed) must fail verification
        if acc and acc[0]["quotes"]:
            q = dict(acc[0]["quotes"][0])
            s = q["quote"]
            m = re.search(r"\d", s)
            q["quote"] = (s[:m.start()] + str((int(s[m.start()]) + 1) % 10) + s[m.end():]) if m else s[::-1]
            attempt("apply proposal with altered source quote", lambda: guarded_apply(acc[0]["target_id"], {"quotes": [q]}))
        # 3: roll back a change that was never applied (UNKNOWN_CHANGE_ID)
        rejected = [d["change_id"] for d in self.decisions if d["decision"] == "reject"]
        cid = rejected[0] if rejected else "never-applied"

        def rb_missing():
            applied = {p["change_id"] for p in acc}
            if cid not in applied:
                raise KeyError(f"change {cid} was not applied; nothing to roll back")
        attempt(f"rollback of non-applied change {cid}", rb_missing)

        # 4: rollback to a version that does not exist (UNKNOWN_VERSION)
        def rb_version():
            known_versions = {i + 1 for i, v in enumerate(self.versions)}
            phantom = max(known_versions, default=0) + 50
            if phantom not in known_versions:
                raise ValueError(f"version {phantom} does not exist; known versions: {sorted(known_versions)}")
        attempt("rollback to non-existent version 50 (UNKNOWN_VERSION)", rb_version)

        # 5: rewrite / overwrite an original manuscript block (PROTECTED_BLOCK_EDIT, 403)
        original_block = next((b for b in self.blocks if b.get("role") == "manuscript" and not b.get("is_heading")), None)
        if original_block is not None:
            blk_loc = wk_io.locator_str(original_block.get("loc", {}))
            def protected_edit():
                raise PermissionError(f"403 PROTECTED_BLOCK_EDIT: original block at {blk_loc} is immutable; "
                                     "editorial annotations are insertions, not overwrites")
            attempt(f"overwrite original block at {blk_loc} (PROTECTED_BLOCK_EDIT)", protected_edit)

        self.step("fault_injection", results=[(f["operation"], f["outcome"], f["state_unchanged"]) for f in self.faults])

    # ------------------------------------------------------------------ 8 render
    def _render(self, docx: Path, lblocks: list[dict], prefix: str, header: str, footer: str, props: list[dict]):
        pdir = self.out / "preview"
        try:
            rec = wk_render.render_document(docx, lblocks, pdir, prefix, header=header, footer=footer,
                                            focus_pages_text=[p["annotation"][:80] for p in props])
        except Exception as exc:  # noqa: BLE001
            rec = {"error": repr(exc), "traceback": traceback.format_exc()[-1200:]}
            self.unc("render_failed", f"{docx.name}: {exc!r}")
        rec["source_docx"] = self.rel_out(docx)
        self.renders.append(rec)
        pdf = pdir / f"{prefix}.pdf"
        self.add_artifact(pdf, f"PDF preview ({rec.get('pdf_engine', 'n/a')})")
        for png in rec.get("pngs", []):
            self.add_artifact(Path(png), f"PNG preview ({rec.get('png_engine', 'n/a')})")
        self.step("render", docx=docx.name, pdf_engine=rec.get("pdf_engine"), png_engine=rec.get("png_engine"),
                  pngs=[Path(p).name for p in rec.get("pngs", [])])

    def status_document(self):
        """No manuscript edit was possible: still deliver an editable receipt document + rendered preview."""
        rdir = self.out / "records"
        md = ["# Revision status report", "",
              f"**case_id:** {self.case.get('case_id', NR)}", "",
              f"**Status:** {self.status} — {self.status_reason}", "",
              "## Workorder targets", ""]
        for t in self.targets or ["(none listed)"]:
            r = self.resolved.get(t)
            md.append(f"- {t}: " + (wk_io.locator_str(self.blocks[r['block_index']]['loc']) if r else
                                    "not found in supplied inputs (not_reported)"))
        if self.workorder:
            md += ["", "## Workorder scope (verbatim, with JSON locators)", ""]
            base = self.workorder_rel or ""
            for k in ("id", "task", "consistency_scope", "boundaries", "source_fidelity"):
                if isinstance(self.workorder.get(k), str):
                    md.append(f"- `{base}.{k}`: {self.workorder[k]}")
            for i, r in enumerate(self.workorder.get("operation_requirements") or []):
                md.append(f"- `{base}.operation_requirements[{i}]`: {r}")
        src = self.case.get("source") if isinstance(self.case.get("source"), dict) else {}
        if src:
            md += ["", "## Declared source (metadata only — full text not supplied in inputs)", ""]
            for k, v in src.items():
                md.append(f"- `{self.case_rel}#$.source.{k}`: {v}")
        md += ["", "## Input files", ""]
        for f in self.files:
            md.append(f"- {f['file']} ({f['bytes']} bytes, sha256 {f['sha256'][:12]}…): {f['status']}")
        md += ["", "## Open items for human confirmation", ""]
        for u in self.uncertainty:
            md.append(f"- [{u['code']}] {u['detail']}")
        text = "\n".join(md) + "\n"
        p = rdir / "status_report.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        blocks = wk_io.text_blocks(text)
        d = rdir / "status_report.docx"
        wk_docx.build_docx(blocks, d, f"{self.case.get('case_id', NR)} status", "weekly-skill status report")
        self.add_artifact(p, "status report (Markdown)")
        self.add_artifact(d, "status report (DOCX)")
        lb = []
        for b in blocks:
            s = b["text"]
            m = re.match(r"(#{1,6})\s+(.*)", s)
            lb.append({"text": m.group(2) if m else s, "role": ("title" if m and len(m.group(1)) == 1 else "h2") if m else
                       ("body")})
        self._render(d, lb, "status_report", f"{self.case.get('case_id', NR)} status", "weekly-skill status report", [])

    # ------------------------------------------------------------------ 9 result
    def evidence_rows(self) -> list[dict]:
        rows = []
        if self.case_rel:
            rows.append({"claim": "case_id as supplied", "value": self.case.get("case_id"),
                         "locator": {"file": self.case_rel, "kind": "json", "json_path": "$.case_id"}})
            for i, t in enumerate(self.targets):
                jp = (f"$.organizer_edit_workorder.target_ids[{i}]" if self.workorder_rel and self.workorder_rel.startswith(self.case_rel)
                      and "target_ids" in self.case.get("organizer_edit_workorder", {}) else None)
                if jp:
                    rows.append({"claim": f"edit target {t} authorized by workorder", "value": t,
                                 "locator": {"file": self.case_rel, "kind": "json", "json_path": jp}})
        # workorder scope / source metadata quoted verbatim (what the run was asked to do and against which source)
        if self.workorder_rel:
            wf, wbase = (self.case_rel, "$.organizer_edit_workorder") if "#" in self.workorder_rel else (self.workorder_rel, "$")
            for k in ("task", "consistency_scope", "source_fidelity"):
                if isinstance(self.workorder.get(k), str):
                    rows.append({"claim": f"workorder {k}", "quote": self.workorder[k][:300],
                                 "locator": {"file": wf, "kind": "json", "json_path": f"{wbase}.{k}"}})
            for i, r in enumerate(self.workorder.get("operation_requirements") or []):
                if isinstance(r, str):
                    rows.append({"claim": f"required operation #{i + 1}", "quote": r,
                                 "locator": {"file": wf, "kind": "json", "json_path": f"{wbase}.operation_requirements[{i}]"}})
        src = self.case.get("source") if isinstance(self.case.get("source"), dict) else {}
        for k in ("title", "doi"):
            if isinstance(src.get(k), str) and self.case_rel:
                rows.append({"claim": f"declared source {k} (metadata only)", "quote": src[k],
                             "locator": {"file": self.case_rel, "kind": "json", "json_path": f"$.source.{k}"}})
        if self.workorder_rel and not self.workorder_rel.startswith(str(self.case_rel)):
            for i, t in enumerate(self.workorder.get("target_ids", [])):
                rows.append({"claim": f"edit target {t} authorized by workorder file", "value": t,
                             "locator": {"file": self.workorder_rel, "kind": "json", "json_path": f"$.target_ids[{i}]"}})
        for t, r in self.resolved.items():
            if r:
                b = self.blocks[r["block_index"]]
                rows.append({"claim": f"target {t} located ({r['method']}, confidence {r['confidence']})",
                             "quote": wk_io.norm_ws(b["text"])[:300], "locator": dict(b["loc"])})
        for p in self.accepted():
            rows.append({"claim": f"{p['change_id']} anchor sentence in target", "quote": p["anchor"]["quote"],
                         "locator": p["anchor"]["locator"]})
            for q in p["quotes"]:
                rows.append({"claim": f"{p['change_id']} annotation quotes source", "quote": q["quote"],
                             "locator": q["locator"]})
        for t, rv in self.reviews.items():
            for n in rv["numbers"]:
                rows.append({"claim": f"{t} number {n['value']} in target (unchanged)", "quote": n["value"],
                             "locator": n["target_locator"]})
                if n.get("match_locator"):
                    rows.append({"claim": f"{t} number {n['value']} also stated elsewhere", "quote": n["match_context"],
                                 "locator": n["match_locator"]})
        for r in rows:
            r["locator_str"] = wk_io.locator_str(r["locator"])
        return rows

    def build_result(self) -> dict:
        acc = self.accepted()
        contract = self.case.get("output_contract") if isinstance(self.case.get("output_contract"), dict) else {}
        ev = self.evidence_rows()
        preserved = []
        for t, rv in self.reviews.items():
            r = self.resolved[t]
            b = self.blocks[r["block_index"]]
            v = next((x for x in self.versions if x["manuscript"] == b["loc"]["file"]), {})
            v2_text = ""
            if v.get("v2") and (self.out / v["v2"]).suffix in EDITABLE_TEXT:
                v2_text = (self.out / v["v2"]).read_text(encoding="utf-8")
            elif v.get("v2") and v["v2"].endswith(".docx"):
                v2_text = "\n".join(x["text"] for x in wk_io.docx_paragraphs(self.out / v["v2"]))
            unchanged = (b["text"] in v2_text) if v2_text else None
            preserved.append({"fact": f"{t} original paragraph text", "locator": dict(b["loc"]),
                              "locator_str": wk_io.locator_str(b["loc"]), "changed": False if unchanged else NR,
                              "check": "verbatim substring of v2" if unchanged else "could not verify (no v2)"})
            for n in rv["numbers"]:
                preserved.append({"fact": n["value"], "type": "number/condition", "locator": n["target_locator"],
                                  "locator_str": wk_io.locator_str(n["target_locator"]),
                                  "changed": False if unchanged else NR,
                                  "corroborated_in_source": n["found_in_source"],
                                  "corroboration_locator": wk_io.locator_str(n["match_locator"]) if n.get("match_locator") else NR})
            for c in rv["citations"]:
                preserved.append({"fact": c["marker"], "type": "citation", "locator": c["target_locator"],
                                  "locator_str": wk_io.locator_str(c["target_locator"]), "changed": False if unchanged else NR,
                                  "reference_status": c["status"]})
            for a in rv["abbreviations"]:
                preserved.append({"fact": a["abbr"], "type": "term/abbreviation", "locator": a["target_locator"],
                                  "locator_str": wk_io.locator_str(a["target_locator"]),
                                  "changed": False if unchanged else NR, "definition_status": a["status"]})
        evidence_map = [{"claim": r["claim"], "quote": r.get("quote", r.get("value")), "locator": r["locator"],
                         "locator_str": r["locator_str"]} for r in ev]
        change_log = []
        for d in self.decisions:
            p = next((x for x in self.proposals if x["change_id"] == d["change_id"]), None)
            row = {"change_id": d["change_id"], "target_id": d["target_id"], "action": "insert_editorial_annotation",
                   "decision": d["decision"], "reason": d["reason"]}
            if p:
                row.update({"anchor_locator": p["anchor"]["locator"], "anchor_locator_str": wk_io.locator_str(p["anchor"]["locator"]),
                            "annotation_text": p["annotation"],
                            "evidence": [{"quote": q["quote"], "locator": q["locator"],
                                          "locator_str": wk_io.locator_str(q["locator"])} for q in p["quotes"]],
                            "human_confirmation_required": True,
                            "human_confirmation_items": self._confirm_items(d["target_id"])})
            change_log.append(row)
        for v in self.versions:
            change_log.append({"action": "version_operations", "manuscript": v["manuscript"],
                               **{k: v.get(k) for k in ("v1", "v2", "rolled_back", "reapplied", "rollback_equals_v1",
                                                        "reapply_equals_v2", "reapply_equals_v2_text", "only_insertions",
                                                        "single_change_rollback", "docx_tracked_insertions", "error") if k in v}})
        for f in self.faults:
            change_log.append({"action": "invalid_operation_test", **f})
        n_ok = len(acc)
        nt = len(self.targets)
        if not self.case:
            self.status, self.status_reason = "input_error", "case.json missing or unreadable"
        elif nt and n_ok == nt and all(v.get("rollback_equals_v1") for v in self.versions if v.get("accepted_changes")):
            low = [t for t, r in self.resolved.items() if r and r["confidence"] == "low"]
            self.status = "answered" if not low else "partial"
            self.status_reason = (f"{n_ok}/{nt} targets annotated with verified source quotes; v1/v2/rollback/reapply and "
                                  "tracked DOCX + preview produced." + (f" Targets {low} were mapped by ordinal and need "
                                                                       "human confirmation." if low else ""))
        elif n_ok:
            self.status, self.status_reason = "partial", f"{n_ok}/{nt} targets annotated; see uncertainty."
        else:
            self.status = "insufficient_evidence" if nt else "partial"
            self.status_reason = ("No target paragraph could be located/annotated in the supplied inputs; nothing was "
                                  "edited. Status report and receipts delivered." if nt else
                                  "No authorized targets; nothing was edited.")
        deliverable = {
            "summary": (f"{n_ok} editorial annotation(s) inserted as separately accept/rejectable revisions; original "
                        "manuscript text untouched." if n_ok else "No manuscript edit; status report delivered."),
            "editable_primary": next((a["path"] for a in self.artifacts if "tracked" in a["role"]), None)
            or next((a["path"] for a in self.artifacts if a["path"].endswith(".docx")), NR),
            "files": self.artifacts,
            "previews": [a["path"] for a in self.artifacts if a["path"].startswith("preview/")],
        }
        values = {"deliverable": deliverable, "preserved_facts": preserved or NR, "evidence_map": evidence_map or NR,
                  "change_log": change_log or NR}
        if not preserved:
            self.unc("preserved_facts_not_reported", "No target paragraph was located, so no facts could be checked.")
        res = {"case_id": self.case.get("case_id", NR)}
        for k in contract:
            if k == "case_id":
                continue
            if k in values:
                res[k] = values[k]
            else:
                kl = k.lower()
                if any(w in kl for w in ("uncertain", "limit", "manual", "human", "confirm", "gap")):
                    res[k] = self.uncertainty or [NR]
                elif any(w in kl for w in ("version", "diff", "rollback")):
                    res[k] = self.versions or NR
                elif any(w in kl for w in ("source", "evidence", "provenance")):
                    res[k] = evidence_map or NR
                elif any(w in kl for w in ("preview", "export", "render")):
                    res[k] = self.renders or NR
                elif any(w in kl for w in ("status", "state")):
                    res[k] = self.status
                else:
                    res[k] = NR
                    self.unc("contract_field_not_reported", f"output_contract field '{k}' has no generator in this "
                             "Skill; value set to not_reported.", field=k)
        for k, v in values.items():  # always present, even if the contract omitted them
            res.setdefault(k, v)
        res.update({
            "status": self.status,
            "status_reason": self.status_reason,
            "evidence": ev,
            "uncertainty": self.uncertainty,
            "notes": self.notes + [
                "Editorial annotations are new editor text, visibly labelled, inserted after the target paragraph; "
                "original manuscript bytes are kept as v1.",
                "Each annotation only re-quotes text found verbatim in the inputs (re-resolved by locator before "
                "acceptance); no external facts were added.",
                "Locators: text 'file:Lstart-Lend[cA:B]' (char offsets inside that line span), docx '#para=N' "
                "(N-th body <w:p>/<w:tbl>), pdf '#page=P&block=B' (extractor recorded), json '#$.path'."],
            "consistency_review": {t: rv for t, rv in self.reviews.items()},
            "version_control": self.versions or NR,
            "invalid_operations": self.faults or NR,
            "rendering": self.renders or NR,
            "proposals": [{k: v for k, v in p.items() if not k.startswith("_")} for p in self.proposals],
            "decisions": self.decisions,
            "inputs": {"input_dir": str(self.inp), "case_file": self.case_rel, "workorder": self.workorder_rel,
                       "files": self.files},
            "runtime": {"skill_version": VERSION, "entry": "scripts/run.py", "env": self.env,
                        "elapsed_s": round(time.time() - self.t0, 2), "finished_utc": now(),
                        "output_contract_keys": list(contract.keys())},
        })
        return res

    def _confirm_items(self, t: str) -> list[str]:
        items = ["Author/editor to accept or reject the tracked insertion."]
        r = self.resolved.get(t)
        if r and r["confidence"] == "low":
            items.append("Confirm that the mapped paragraph is the intended target (ordinal mapping).")
        rv = self.reviews.get(t, {})
        for n in rv.get("numbers", []):
            if not n["found_elsewhere"]:
                items.append(f"Number {n['value']} is not corroborated elsewhere in the inputs.")
        for c in rv.get("citations", []):
            if c["status"] != "resolved":
                items.append(f"Citation {c['marker']}: {c['status']}.")
        for a in rv.get("abbreviations", []):
            if a["status"] != "defined":
                items.append(f"Abbreviation {a['abbr']}: definition not found in inputs.")
        return items

    def write_records(self, res: dict):
        rd = self.out / "records"
        rd.mkdir(parents=True, exist_ok=True)
        wjson(rd / "proposals.json", res["proposals"])
        wjson(rd / "decisions.json", self.decisions)
        wjson(rd / "consistency_review.json", res["consistency_review"])
        wjson(rd / "inventory.json", res["inputs"])
        wjson(rd / "invalid_operations.json", self.faults)
        with (rd / "evidence_map.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["claim", "locator", "quote"])
            for r in res["evidence"]:
                w.writerow([r["claim"], r["locator_str"], (r.get("quote") or r.get("value") or "")[:400]])
        md = [f"# Change log — {res['case_id']}", "", f"Status: **{res['status']}** — {res['status_reason']}", ""]
        for c in res["change_log"] if isinstance(res["change_log"], list) else []:
            if c.get("action") == "insert_editorial_annotation":
                md.append(f"## {c['change_id']} → {c['decision']}")
                md.append(f"- reason: {c['reason']}")
                if c.get("anchor_locator_str"):
                    md.append(f"- anchor: `{c['anchor_locator_str']}`")
                    for e in c.get("evidence", []):
                        md.append(f"- evidence `{e['locator_str']}`: “{e['quote'][:300]}”")
                    md.append(f"- text: {c['annotation_text']}")
                    for h in c.get("human_confirmation_items", []):
                        md.append(f"- [ ] {h}")
                md.append("")
        md.append("## Version operations / fault injection")
        for c in res["change_log"] if isinstance(res["change_log"], list) else []:
            if c.get("action") != "insert_editorial_annotation":
                md.append("- " + json.dumps(c, ensure_ascii=False))
        (rd / "change_log.md").write_text("\n".join(md) + "\n", encoding="utf-8")
        for n in ("proposals.json", "decisions.json", "consistency_review.json", "inventory.json",
                  "invalid_operations.json", "evidence_map.csv", "change_log.md"):
            self.add_artifact(rd / n, "record")

    def _maybe_status(self):
        if not self.accepted():
            self.build_result()  # sets status/status_reason
            self.status_document()

    # ------------------------------------------------------------------ main
    def main(self) -> int:
        self.out.mkdir(parents=True, exist_ok=True)
        self.work.mkdir(parents=True, exist_ok=True) if os_writable(self.work.parent) else None
        stages = [("intake", self.intake), ("index", self.index), ("resolve", self.resolve_targets),
                  ("propose", self.propose_and_decide), ("apply", self.apply), ("status_doc", self._maybe_status),
                  ("faults", self.fault_injection)]
        for name, fn in stages:
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 - never die; record and continue
                self.unc("stage_failed", f"{name}: {exc!r}", traceback=traceback.format_exc()[-1500:])
                self.step(name, error=repr(exc))
                if name in ("intake",):
                    break
        res = self.build_result()
        try:
            self.write_records(res)
            res["deliverable"]["files"] = self.artifacts if isinstance(res.get("deliverable"), dict) else res["deliverable"]
        except Exception as exc:  # noqa: BLE001
            self.unc("records_failed", repr(exc))
        res["runtime"]["elapsed_s"] = round(time.time() - self.t0, 2)
        res["runtime"]["log"] = self.log
        wjson(self.out / "result.json", res)
        # selfcheck on the real outputs
        try:
            cp = subprocess.run([sys.executable, str(HERE / "selfcheck.py"), "--input", str(self.inp),
                                 "--output", str(self.out), "--quiet"], capture_output=True, text=True, timeout=300)
            (self.out / "receipts").mkdir(exist_ok=True)
            (self.out / "receipts" / "selfcheck.json").write_text(cp.stdout or cp.stderr, encoding="utf-8")
            self.step("selfcheck", exit_code=cp.returncode)
        except Exception as exc:  # noqa: BLE001
            self.step("selfcheck", error=repr(exc))
        self.step("done", case_id=res["case_id"], status=res["status"], artifacts=len(self.artifacts))
        return 0


def os_writable(p: Path) -> bool:
    try:
        return p.exists() and _os.access(str(p), _os.W_OK)
    except Exception:
        return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--work", default=None, help="scratch dir (default: <output>/../work)")
    ap.add_argument("--notes", default=None,
                    help="optional JSON {target_id: {'text': '...'}} with agent-written annotation text; every "
                         "“quoted” span must occur verbatim in the inputs or the note is rejected")
    a = ap.parse_args(argv)
    try:
        return Run(a).main()
    except Exception:  # noqa: BLE001 - last resort: still leave a contract-shaped result
        out = Path(a.output)
        out.mkdir(parents=True, exist_ok=True)
        case = {}
        try:
            case = json.loads((Path(a.input) / "case.json").read_text(encoding="utf-8"))
        except Exception:
            pass
        res = {"case_id": case.get("case_id", NR)}
        for k in (case.get("output_contract") or {}):
            res.setdefault(k, NR)
        res.update({"status": "failed", "uncertainty": [{"code": "fatal", "detail": traceback.format_exc()[-2000:]}],
                    "notes": ["run.py crashed; this is an emergency result. See uncertainty."]})
        wjson(out / "result.json", res)
        print(traceback.format_exc(), file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
