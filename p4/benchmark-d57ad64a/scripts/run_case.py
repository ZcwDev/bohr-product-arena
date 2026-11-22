#!/usr/bin/env python3
"""One-case driver for the academic-writing skill: prepare -> (agent writes) -> finalize.

  prepare  : read /input/case.json, discover the REAL material files the case
             points to (PDF / md / txt / tex / bib / docx / inline text), index
             them into page/line-anchored blocks, derive the scoring spec, and
             write an honest early result.json (status=partial) so a timeout
             never leaves /app/output empty.
  finalize : take the agent's draft (default /app/output/draft.md) and its
             filled result.json, then mechanically: verify every quote, diff
             numbers draft-vs-material, export draft -> .docx (editable) +
             .pdf + .png (real rendered previews, with font fallback and
             structured missing-glyph warnings), run contract check, the
             writebench scorer, the report, and the gain metrics.

Nothing here writes prose or invents facts. Every value it adds to result.json
is either copied from the material with a locator or is a check outcome.

CLI:
  python3 run_case.py prepare  --case /input/case.json --work /app/work --output /app/output
  python3 run_case.py finalize --case /input/case.json --work /app/work --output /app/output
                               [--draft /app/output/draft.md] [--font "Liberation Serif"]
                               [--budget 110]
Exit codes: 0 done (status may still be partial/insufficient_evidence, read it);
            2 input_error (case unreadable); never raises on a bad material file.
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
import glob
import json
import os
import re
import subprocess
import sys
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import evidence as evidence_mod  # noqa: E402

T0 = time.time()
TEXT_EXT = (".md", ".txt", ".tex", ".bib", ".rst", ".csv", ".ris")
DOC_EXT = (".pdf", ".docx") + TEXT_EXT
INSTRUCTION_KEYS = ("instruction", "task", "requirement", "constraint", "audience", "style",
                    "format", "genre", "word", "output_contract", "case_id", "title")
FONT_DIRS = ["/usr/share/fonts", "/usr/local/share/fonts", "/Library/Fonts", "/System/Library/Fonts",
             os.path.expanduser("~/.fonts"), os.path.expanduser("~/Library/Fonts"), "/app/fonts",
             "/input", "/skills/weekly-skill/fonts"]


def log(msg: str) -> None:
    print(f"[run_case +{time.time() - T0:5.1f}s] {msg}", file=sys.stderr)


def jdump(obj, path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)


def run(cmd, timeout=60):
    """Run a helper script; return (exit_code, stdout, stderr). Never raises."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"
    except Exception as exc:  # noqa: BLE001
        return 127, "", str(exc)


# ------------------------------------------------------------------ material discovery

def _walk_strings(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk_strings(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_strings(v, f"{path}[{i}]")
    elif isinstance(obj, str):
        yield path, obj


def discover(case: dict, case_path: str) -> tuple[list[str], list[dict], list[dict]]:
    """Return (material_files, inline_materials, warnings)."""
    roots = [os.path.dirname(os.path.abspath(case_path)), "/input", os.getcwd()]
    files, inline, warnings = [], [], []
    for key, value in _walk_strings(case):
        low = key.lower()
        if value.lower().endswith(DOC_EXT) or "/" in value and os.path.splitext(value)[1].lower() in DOC_EXT:
            hit = None
            for root in [""] + roots:
                cand = value if os.path.isabs(value) else os.path.join(root, value)
                if os.path.isfile(cand):
                    hit = os.path.abspath(cand)
                    break
            if hit:
                files.append(hit)
            else:
                warnings.append({"type": "material_missing", "field": key, "path": value,
                                 "effect": "referenced by the case but not found; not used"})
            continue
        if len(value) >= 80 and not any(t in low for t in INSTRUCTION_KEYS):
            inline.append({"field": key, "text": value})
    # files that sit next to the case (the platform ships the PDF beside case.json)
    for root in roots[:2]:
        for path in sorted(glob.glob(os.path.join(root, "*"))):
            if (os.path.isfile(path) and path.lower().endswith(DOC_EXT)
                    and os.path.abspath(path) != os.path.abspath(case_path)
                    and os.path.basename(path) not in ("task.md",)):
                files.append(os.path.abspath(path))
    seen, uniq = set(), []
    for f in files:
        if f not in seen:
            seen.add(f)
            uniq.append(f)
    return uniq, inline, warnings


def index_file(path: str, work: str) -> tuple[list[dict], dict]:
    name = os.path.basename(path)
    ext = os.path.splitext(path)[1].lower()
    info = {"file": path, "kind": ext.lstrip("."), "blocks": 0, "warnings": []}
    blocks = []
    if ext == ".pdf":
        out = os.path.join(work, "extract", name + ".json")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        code, so, se = run([sys.executable, os.path.join(HERE, "pdf_text.py"), "extract", path, "-o", out], 90)
        info["exit_code"] = code
        if code != 0 or not os.path.isfile(out):
            info["warnings"].append({"type": "pdf_extract_failed", "detail": (se or so)[-300:]})
            return [], info
        data = json.load(open(out, encoding="utf-8"))
        info["engine"] = data.get("engine")
        info["pages"] = data.get("page_count")
        info["warnings"] += [{"type": "pdf_extract", "detail": w} for w in data.get("warnings", [])]
        for b in data.get("blocks", []):
            blocks.append({"locator": b["id"],
                           "file": name, "page": b.get("page"), "type": b.get("type"),
                           "section": b.get("section"), "text": b.get("text", "")})
        if not blocks:
            info["warnings"].append({"type": "no_text_layer",
                                     "detail": "PDF has no extractable text (scanned?); not OCR'd"})
    elif ext == ".docx":
        try:
            with zipfile.ZipFile(path) as zf:
                xml = zf.read("word/document.xml").decode("utf-8", "replace")
            for i, para in enumerate(re.findall(r"<w:p[ >].*?</w:p>", xml, re.S), start=1):
                text = "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", para))
                if text.strip():
                    blocks.append({"locator": f"{name}:p{i}", "file": name, "type": "body", "text": text})
        except Exception as exc:  # noqa: BLE001
            info["warnings"].append({"type": "docx_unreadable", "detail": str(exc)[:200]})
    else:
        try:
            raw = open(path, encoding="utf-8", errors="replace").read()
            for n, line in enumerate(raw.splitlines(), start=1):
                if line.strip():
                    blocks.append({"locator": f"{name}:{n}", "file": name, "type": "body", "text": line})
        except OSError as exc:
            info["warnings"].append({"type": "unreadable", "detail": str(exc)[:200]})
    info["blocks"] = len(blocks)
    return blocks, info


def build_index(case, case_path, work):
    files, inline, warnings = discover(case, case_path)
    blocks, sources = [], []
    multi_pdf = sum(1 for f in files if f.lower().endswith(".pdf")) > 1
    for f in files:
        b, info = index_file(f, work)
        if multi_pdf and f.lower().endswith(".pdf"):  # keep locators unique across PDFs
            for blk in b:
                blk["locator"] = f"{blk['file']}#{blk['locator']}"
        blocks += b
        sources.append(info)
    for item in inline:
        for n, line in enumerate(item["text"].splitlines(), start=1):
            if line.strip():
                blocks.append({"locator": f"case.json:{item['field']}:L{n}", "file": "case.json",
                               "type": "inline", "text": line})
        sources.append({"file": "case.json", "field": item["field"], "kind": "inline",
                        "blocks": len(item["text"].splitlines())})
    return blocks, sources, warnings


# ------------------------------------------------------------------ prepare

def _contract(case):
    c = case.get("output_contract")
    return c if isinstance(c, dict) and c else None


def _not_reported(reason):
    return {"fact": "not_reported", "reason": reason}


def cmd_prepare(a) -> int:
    os.makedirs(a.work, exist_ok=True)
    os.makedirs(a.output, exist_ok=True)
    manifest = {"case": a.case, "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "steps": [], "warnings": []}
    try:
        case = json.load(open(a.case, encoding="utf-8"))
        assert isinstance(case, dict)
    except Exception as exc:  # noqa: BLE001
        result = {"case_id": "not_reported", "status": "input_error",
                  "deliverable": {"path": "not_reported", "reason": f"case unreadable: {exc}"},
                  "preserved_facts": [_not_reported("case file unreadable")],
                  "evidence_map": [{"claim": "not_reported", "locator": "none", "reason": "case unreadable"}],
                  "change_log": [{"action": "none", "reason": "case file unreadable", "human_check": True}],
                  "limitations": [f"input_error: {a.case} could not be parsed as a JSON object ({exc})"]}
        jdump(result, os.path.join(a.output, "result.json"))
        log(f"input_error: {exc}")
        return 2

    case_id = case.get("case_id") or "not_reported"
    contract = _contract(case)
    blocks, sources, warnings = build_index(case, a.case, a.work)
    manifest["warnings"] += warnings
    for s in sources:
        manifest["warnings"] += [dict(w, file=s.get("file")) for w in s.get("warnings", [])]
    jdump({"blocks": blocks}, os.path.join(a.work, "blocks.json"))
    with open(os.path.join(a.work, "material.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(b["text"] for b in blocks))
    manifest["sources"] = sources
    manifest["block_count"] = len(blocks)
    log(f"indexed {len(blocks)} blocks from {len(sources)} sources")

    spec_path = os.path.join(a.work, "spec.json")
    code, so, se = run([sys.executable, os.path.join(HERE, "writebench.py"), "spec", a.case, "-o", spec_path])
    manifest["steps"].append({"step": "writebench spec", "exit": code})
    if code == 0:
        code2, so2, _ = run([sys.executable, os.path.join(HERE, "writebench.py"), "selfcheck", spec_path])
        manifest["steps"].append({"step": "writebench selfcheck", "exit": code2, "stdout": so2[-400:]})

    # inventory of numbers actually present in the material, with locators
    index = evidence_mod.LocatorIndex({"blocks": blocks})
    numbers = index.numbers(limit=400)
    jdump({"numbers": numbers}, os.path.join(a.work, "facts.json"))

    instr = {k: v for k, v in case.items() if k not in ("output_contract",) and isinstance(v, (str, int, float))
             and any(t in k.lower() for t in INSTRUCTION_KEYS)}
    jdump(instr, os.path.join(a.work, "constraints.json"))

    limitations = []
    if not contract:
        limitations.append("case has no output_contract object; fields defaulted to the writing family")
    if not blocks:
        status = "insufficient_evidence"
        limitations.append("no readable source material found in the case (no material file, no inline "
                           "material text); no fact can be preserved or sourced")
    else:
        status = "partial"
        limitations.append("prepare stage only: draft and evidence_map are written by the agent next; "
                           "this early file exists so a timeout never leaves output empty")
    for w in warnings:
        limitations.append(f"{w['type']}: {w.get('path') or w.get('detail')}")
    facts = ([{"fact": n["value"], "kind": "number", "source": n["locator"], "changed": False,
               "context": n["sentence"][:160]} for n in numbers[:60]]
             or [_not_reported("material contains no numeric fact" if blocks else "no material available")])
    result = {
        "case_id": case_id,
        "status": status,
        "deliverable": {"path": os.path.join(a.output, "draft.md"), "format": "markdown",
                        "state": "not_written_yet"},
        "preserved_facts": facts,
        "evidence_map": [{"claim": "not_reported", "locator": "none",
                          "reason": "evidence is mapped after the agent writes the draft"}],
        "change_log": [{"action": "prepare", "reason": "indexed material and derived the scoring spec",
                        "human_check": False, "changed_fields": ["preserved_facts"]}],
        "limitations": limitations,
    }
    for key in (contract or {}):
        result.setdefault(key, {"value": "not_reported", "reason": "field not yet produced"})
    out = os.path.join(a.output, "result.json")
    if not os.path.isfile(out) or a.force:
        jdump(result, out)
    manifest["early_status"] = status
    manifest["elapsed_s"] = round(time.time() - T0, 2)
    jdump(manifest, os.path.join(a.work, "prepare.json"))
    print(json.dumps({"case_id": case_id, "status": status, "blocks": len(blocks),
                      "numbers": len(numbers), "sources": [s.get("file") for s in sources],
                      "warnings": manifest["warnings"], "elapsed_s": manifest["elapsed_s"]},
                     ensure_ascii=False, indent=1))
    return 0


# ------------------------------------------------------------------ rendering

def md_blocks(md: str) -> list[dict]:
    out, para, table = [], [], []

    def flush():
        if para:
            out.append({"type": "p", "text": " ".join(para)})
            para.clear()
        if table:
            out.append({"type": "table", "rows": [r for r in table if not re.match(r"^[\s|:\-]+$", "|".join(r))]})
            table.clear()
    for line in md.splitlines():
        s = line.rstrip()
        if not s.strip():
            flush(); continue
        m = re.match(r"^(#{1,6})\s+(.*)", s)
        if m:
            flush(); out.append({"type": "h1" if len(m.group(1)) <= 1 else "h2", "text": m.group(2)}); continue
        if s.lstrip().startswith("|"):
            if para:
                out.append({"type": "p", "text": " ".join(para)}); para.clear()
            table.append([c.strip() for c in s.strip().strip("|").split("|")]); continue
        m = re.match(r"^(\s*)(?:[-*+]|\d+[.)])\s+(.*)", s)
        if m:
            flush(); out.append({"type": "bullet", "text": m.group(2), "level": len(m.group(1)) // 2}); continue
        if table:
            flush()
        para.append(s.strip())
    flush()
    return out


def find_font(name: str | None) -> str | None:
    if not name:
        return None
    key = re.sub(r"[\s_-]", "", name).lower()
    for d in FONT_DIRS:
        for path in glob.glob(os.path.join(d, "**", "*"), recursive=True):
            if path.lower().endswith((".ttf", ".otf", ".ttc")) and key in re.sub(r"[\s_-]", "", os.path.basename(path)).lower():
                return path
    return None


def _html(blocks):
    from html import escape
    parts = []
    for b in blocks:
        t = b.get("type")
        if t == "h1":
            parts.append(f"<h1>{escape(b['text'])}</h1>")
        elif t == "h2":
            parts.append(f"<h2>{escape(b['text'])}</h2>")
        elif t == "bullet":
            parts.append(f"<ul><li>{escape(b['text'])}</li></ul>")
        elif t == "table":
            rows = "".join("<tr>" + "".join(f"<td>{escape(c)}</td>" for c in r) + "</tr>" for r in b["rows"])
            parts.append(f"<table border='1' cellspacing='0' cellpadding='3'>{rows}</table>")
        else:
            parts.append(f"<p>{escape(b['text'])}</p>")
    return "\n".join(parts)


def render_pdf_png(md: str, pdf_path: str, png_dir: str, font: str | None) -> dict:
    """Markdown -> PDF (PyMuPDF Story) -> PNG pages; matplotlib fallback; never raises."""
    rep = {"ok": False, "engine": None, "pdf": None, "png": [], "font_requested": font,
           "font_used": None, "warnings": []}
    blocks = md_blocks(md)
    try:
        import fitz  # type: ignore
        font_path = find_font(font)
        css = "body{font-size:10.5pt;line-height:1.35;} h1{font-size:16pt;} h2{font-size:13pt;} td{font-size:9pt;}"
        archive = None
        if font and font_path:
            archive = fitz.Archive(os.path.dirname(font_path))
            css = (f"@font-face{{font-family:'UserFont';src:url({os.path.basename(font_path)});}}"
                   f"body{{font-family:'UserFont',serif;}}" + css)
            rep["font_used"] = font_path
            try:
                fobj = fitz.Font(fontfile=font_path)
                missing = sorted({ch for ch in md if not ch.isspace() and not fobj.has_glyph(ord(ch))})
                if missing:
                    rep["warnings"].append({"type": "missing_glyph", "font": font, "count": len(missing),
                                            "chars": "".join(missing[:40]),
                                            "effect": "rendered with PyMuPDF built-in fallback font"})
            except Exception as exc:  # noqa: BLE001
                rep["warnings"].append({"type": "font_probe_failed", "font": font, "detail": str(exc)[:160]})
        else:
            css = "body{font-family:serif;}" + css
            rep["font_used"] = "PyMuPDF built-in serif (+CJK fallback)"
            if font:
                rep["warnings"].append({"type": "font_not_found", "font": font,
                                        "searched": FONT_DIRS,
                                        "effect": "rendered with PyMuPDF built-in serif; "
                                                  "metrics differ from the requested font"})
        try:  # glyph coverage of the built-in fallback chain (serif + china-s CJK)
            chain = [fitz.Font("china-s"), fitz.Font("tiro")]
            uncovered = sorted({ch for ch in md if not ch.isspace()
                                and not any(f.has_glyph(ord(ch)) for f in chain)})
            if uncovered:
                rep["warnings"].append({"type": "missing_glyph", "font": "fallback chain tiro+china-s",
                                        "count": len(uncovered), "chars": "".join(uncovered[:40]),
                                        "codepoints": [f"U+{ord(c):04X}" for c in uncovered[:40]],
                                        "effect": "these characters may render as blank boxes in the preview; "
                                                  "the .md/.docx text is unaffected"})
        except Exception as exc:  # noqa: BLE001
            rep["warnings"].append({"type": "glyph_probe_failed", "detail": str(exc)[:160]})
        story = fitz.Story(html=_html(blocks), user_css=css, archive=archive)
        writer = fitz.DocumentWriter(pdf_path)
        page_rect = fitz.paper_rect("a4")
        where = page_rect + (56, 56, -56, -56)
        more, guard = 1, 0
        while more and guard < 200:
            dev = writer.begin_page(page_rect)
            more, _ = story.place(where)
            story.draw(dev)
            writer.end_page()
            guard += 1
        writer.close()
        try:  # embed only the glyphs used (keeps CJK PDFs small); best effort
            tmp = fitz.open(pdf_path)
            tmp.subset_fonts()
            tmp.save(pdf_path + ".tmp", garbage=3, deflate=True)
            tmp.close()
            os.replace(pdf_path + ".tmp", pdf_path)
        except Exception as exc:  # noqa: BLE001
            rep["warnings"].append({"type": "font_subset_skipped", "detail": str(exc)[:160]})
        doc = fitz.open(pdf_path)
        os.makedirs(png_dir, exist_ok=True)
        for i, page in enumerate(doc, start=1):
            p = os.path.join(png_dir, f"draft-page-{i:02d}.png")
            page.get_pixmap(dpi=110).save(p)
            rep["png"].append(p)
        rep["pages"] = doc.page_count
        rep["pdf_fonts"] = sorted({f[3] for pg in doc for f in pg.get_fonts()})
        doc.close()
        rep.update(ok=True, engine="pymupdf-story", pdf=pdf_path)
        return rep
    except Exception as exc:  # noqa: BLE001
        rep["warnings"].append({"type": "pymupdf_render_failed", "detail": f"{type(exc).__name__}: {exc}"[:240]})
    try:
        import preview  # noqa: E402
        r = preview.doc_png({"blocks": [b if b["type"] != "table" else
                                        {"type": "p", "text": " | ".join(" / ".join(r) for r in b["rows"])}
                                        for b in blocks]}, png_dir)
        rep.update(ok=bool(r.get("pages")), engine="matplotlib", png=r.get("pages", []))
        rep["warnings"].append({"type": "no_pdf", "effect": "PNG preview only (matplotlib fallback)"})
    except Exception as exc:  # noqa: BLE001
        rep["warnings"].append({"type": "preview_failed", "detail": f"{type(exc).__name__}: {exc}"[:240]})
    return rep


# ------------------------------------------------------------------ finalize

def _num_tokens(text):
    body = re.sub(r"(?m)^\s*#{1,6}.*$", " ", text)
    body = re.sub(r"(?i)\b(?:section|table|figure|fig\.?|step|part)\s+\d+\b", " ", body)
    body = re.sub(r"\bp\d+b\d+\b|[\w./-]+\.[A-Za-z]{2,4}:\d+", " ", body)
    return {evidence_mod.normalize(m.group(0)).strip().replace(",", "")
            for m in evidence_mod.NUMBER_RE.finditer(body) if re.search(r"\d", m.group(0))}


def cmd_finalize(a) -> int:
    deadline = T0 + a.budget
    out_res = os.path.join(a.output, "result.json")
    draft = a.draft or os.path.join(a.output, "draft.md")
    blocks_path = os.path.join(a.work, "blocks.json")
    if not os.path.isfile(blocks_path):
        log("blocks.json missing -> running prepare first")
        a.force = False
        if cmd_prepare(a) == 2:
            return 2
    try:
        case = json.load(open(a.case, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        case = {}
    result = json.load(open(out_res, encoding="utf-8")) if os.path.isfile(out_res) else {}
    blocks = json.load(open(blocks_path, encoding="utf-8"))["blocks"]
    index = evidence_mod.LocatorIndex({"blocks": blocks})
    material_text = "\n".join(b["text"] for b in blocks)
    checks = {"elapsed_start_s": round(time.time() - T0, 2), "warnings": []}
    limitations = [l for l in result.get("limitations", []) if not str(l).startswith("prepare stage only")]
    result["case_id"] = case.get("case_id", result.get("case_id", "not_reported"))
    if result["case_id"] == "EXAMPLE-NOT-A-DEMO-CASE":
        result["example_run"] = True
        limitations.append("run on the task-provided interface example (case_id EXAMPLE-NOT-A-DEMO-CASE); "
                           "it is not a formal evaluation case and carries no scientific material")

    # 1) deliverable present?
    if not os.path.isfile(draft):
        limitations.append(f"deliverable missing: {draft} was not written; status capped at partial")
        draft_text = ""
    else:
        draft_text = open(draft, encoding="utf-8", errors="replace").read()
    deliverable = result.get("deliverable") if isinstance(result.get("deliverable"), dict) else {}
    deliverable.update({"path": draft, "format": "markdown", "bytes": len(draft_text.encode("utf-8")),
                        "lines": draft_text.count("\n") + (1 if draft_text else 0)})
    deliverable.pop("state", None)

    # 2) quotes in evidence_map must be verbatim in the material
    emap = [e for e in (result.get("evidence_map") or []) if isinstance(e, dict)]
    real = [e for e in emap if e.get("claim") not in (None, "not_reported")]
    bad_quotes = []
    for e in real:
        q = e.get("quote")
        if not q:
            e["verified"] = False
            e["verification"] = "no quote given"
            bad_quotes.append(e.get("claim", "")[:80])
            continue
        v = evidence_mod.verify_quote(index, q)
        e["verified"] = bool(v["exact"])
        if v["found"]:
            if e.get("locator") != v["locator"]:
                e["locator_checked"] = v["locator"]
        else:
            bad_quotes.append(q[:80])
        e["verification"] = "exact" if v["exact"] else ("partial" if v["found"] else "not_found_in_material")
    if real:
        result["evidence_map"] = real
    else:
        result["evidence_map"] = [{"claim": "not_reported", "locator": "none",
                                   "reason": ("case supplies no material, so no claim can be sourced"
                                              if not blocks else
                                              "the agent mapped no claim; every claim in the draft is unsourced")}]
        if blocks and draft_text:
            limitations.append("evidence_map is empty although material exists: claims in the draft are unsourced")
    checks["quotes_total"] = len(real)
    checks["quotes_not_verbatim"] = bad_quotes
    if bad_quotes:
        limitations.append(f"{len(bad_quotes)} evidence quote(s) not found verbatim in the material; "
                           "they need human confirmation: " + "; ".join(bad_quotes[:5]))

    # 3) numbers: draft vs material
    src, dst = _num_tokens(material_text), _num_tokens(draft_text)
    hay = evidence_mod.normalize(material_text).replace(",", "")
    invented = sorted(t for t in dst if t not in src and t not in hay)
    checks["numbers_in_draft"] = len(dst)
    checks["numbers_not_in_material"] = invented
    if invented and blocks:
        limitations.append("numbers in the draft not found in the material (verify or remove): "
                           + ", ".join(invented[:15]))

    # 4) editable export + real previews
    if draft_text and time.time() < deadline - 15:
        import ooxml  # noqa: E402
        docx_path = os.path.join(a.output, "draft.docx")
        try:
            ooxml.build_docx({"title": None, "blocks": md_blocks(draft_text)}, docx_path)
            deliverable["editable_export"] = docx_path
        except Exception as exc:  # noqa: BLE001
            checks["warnings"].append({"type": "docx_export_failed", "detail": str(exc)[:200]})
        font = a.font or (case.get("font") if isinstance(case.get("font"), str) else None)
        if not font:
            m = re.search(r"(Liberation (?:Serif|Sans)|Times New Roman|Arial|SimSun|宋体|Calibri)",
                          json.dumps(case, ensure_ascii=False))
            font = m.group(1) if m else None
        rep = render_pdf_png(draft_text, os.path.join(a.output, "draft.pdf"),
                             os.path.join(a.output, "preview"), font)
        checks["render"] = rep
        checks["warnings"] += rep["warnings"]
        deliverable["preview_pdf"] = rep.get("pdf") or "not_reported"
        deliverable["preview_png"] = rep.get("png") or ["not_reported"]
        deliverable["render_engine"] = rep.get("engine") or "none"
        for w in rep["warnings"]:
            limitations.append(f"render warning {w['type']}: {w.get('effect') or w.get('detail')}")
    elif draft_text:
        limitations.append("time budget nearly spent: docx/PDF/PNG export skipped")
    result["deliverable"] = deliverable

    # 5) honest status
    status = result.get("status") or "partial"
    if not blocks:
        status = "insufficient_evidence"
    elif not draft_text:
        status = "partial"
    elif bad_quotes or invented:
        status = "partial" if status == "answered" else status
    result["status"] = status

    # 6) every contract field present and non-empty (not_reported + reason if truly absent)
    for key in (_contract(case) or {}):
        if result.get(key) in (None, "", [], {}):
            result[key] = [{"value": "not_reported", "reason": "the run produced nothing for this field"}] \
                if key != "deliverable" else {"path": "not_reported", "reason": "no deliverable written"}
    cl = result.get("change_log") or []
    cl.append({"action": "finalize_checks", "reason": "quotes verified, numbers diffed, exports rendered",
               "human_check": bool(bad_quotes or invented), "changed_fields": ["evidence_map", "deliverable",
                                                                                "status", "limitations"]})
    result["change_log"] = cl
    if status != "answered" and not limitations:
        limitations.append(f"status={status}: see change_log entries marked human_check=true")
    result["limitations"] = list(dict.fromkeys(limitations)) or ["none beyond the checks recorded in checks.json"]
    jdump(result, out_res)

    # 7) mechanical self-checks (each bounded; skipped honestly when out of time)
    py = sys.executable
    steps = [
        ("contract", [py, os.path.join(HERE, "contract.py"), "check", out_res, "--family", "writing",
                      "--contract", a.case]),
        ("text_checks_result", [py, os.path.join(HERE, "text_checks.py"), "result", out_res, "--family", "writing"]),
        ("score", [py, os.path.join(HERE, "writebench.py"), "score", os.path.join(a.work, "spec.json"), out_res,
                   "--blocks", blocks_path, "--deliverable", draft,
                   "-o", os.path.join(a.output, "score.json"),
                   "--failures", os.path.join(a.output, "failures.jsonl")]),
        ("report", [py, os.path.join(HERE, "writebench.py"), "report", os.path.join(a.output, "score.json"),
                    "-o", os.path.join(a.output, "report.md")]),
        ("gain", [py, os.path.join(HERE, "gain_report.py"), "report", "--result", out_res, "--blocks",
                  blocks_path, "--family", "writing", "-o", os.path.join(a.work, "gain.json")]),
    ]
    checks["steps"] = []
    for name, cmd in steps:
        if time.time() > deadline - 5:
            checks["steps"].append({"step": name, "exit": None, "skipped": "time budget"})
            continue
        if name == "score" and not os.path.isfile(os.path.join(a.work, "spec.json")):
            checks["steps"].append({"step": name, "exit": None, "skipped": "no spec.json (case lacks output_contract)"})
            continue
        if name == "report" and not os.path.isfile(os.path.join(a.output, "score.json")):
            checks["steps"].append({"step": name, "exit": None, "skipped": "no score.json"})
            continue
        code, so, se = run(cmd, 40)
        checks["steps"].append({"step": name, "exit": code, "stdout_tail": so[-600:], "stderr_tail": se[-300:]})
    checks["elapsed_total_s"] = round(time.time() - T0, 2)
    jdump(checks, os.path.join(a.output, "checks.json"))
    print(json.dumps({"case_id": result["case_id"], "status": status,
                      "steps": {s["step"]: s["exit"] for s in checks["steps"]},
                      "quotes_not_verbatim": len(bad_quotes), "numbers_not_in_material": len(invented),
                      "render_engine": deliverable.get("render_engine"),
                      "warnings": checks["warnings"], "elapsed_s": checks["elapsed_total_s"]},
                     ensure_ascii=False, indent=1))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="prepare/finalize one academic-writing case")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("prepare", "finalize"):
        p = sub.add_parser(name)
        p.add_argument("--case", default="/input/case.json")
        p.add_argument("--work", default="/app/work")
        p.add_argument("--output", default="/app/output")
        p.add_argument("--force", action="store_true", help="prepare: overwrite an existing result.json")
        if name == "finalize":
            p.add_argument("--draft")
            p.add_argument("--font")
            p.add_argument("--budget", type=float, default=110.0)
    a = ap.parse_args()
    return cmd_prepare(a) if a.cmd == "prepare" else cmd_finalize(a)


if __name__ == "__main__":
    sys.exit(main())
