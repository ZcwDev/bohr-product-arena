
"""Evidence-bound writing pipeline (offline). Defaults: case=/input/case.json, work=/app/work, output=/app/output.
Status "answered" requires passing claim map, quotes, numbers, citations, contract and preview gates."""
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
import os as _os, sys as _sys
_TOOLS_PY = _os.environ.get("WEEKLY_TOOLS_PYTHON") or "/opt/weekly-tools/bin/python"
if (_os.path.exists(_TOOLS_PY) and not _os.environ.get("_SKILL_REEXEC")
        and _os.path.realpath(_sys.executable) != _os.path.realpath(_TOOLS_PY)):
    _os.environ["_SKILL_REEXEC"] = "1"
    try:
        _os.execv(_TOOLS_PY, [_TOOLS_PY, _os.path.abspath(__file__)] + _sys.argv[1:])
    except OSError:
        pass

import argparse
import datetime as _dt
import hashlib
import html
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import zipfile
from xml.etree import ElementTree as ET

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import contract
import evidence
import text_checks

TEXT_SUFFIXES = {".txt", ".md", ".tex", ".bib", ".csv", ".tsv", ".jsonl"}
OFFICE_SUFFIXES = {".docx", ".pptx"}
SUPPORTED_SUFFIXES = TEXT_SUFFIXES | OFFICE_SUFFIXES | {".pdf"}
CONTENT_KEYS = ("content", "text", "full_text", "body", "source_text", "manuscript", "draft")
MATERIAL_CONTAINERS = ("material", "materials", "source_material", "documents", "inputs", "draft")
FONT_NAMES = ("Liberation Serif", "Times New Roman", "Noto Serif CJK SC", "Noto Sans CJK SC",
              "Source Han Serif SC", "Source Han Sans SC", "Arial", "Calibri")

def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(value, fh, ensure_ascii=False,indent=2)
            fh.write("\n")
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)

def nr(reason: str, locator: str = "not_reported") -> dict:
    return {"value": "not_reported", "reason": reason, "locator": locator}

def command_record(cmd: list[str], code: int, stdout: str, stderr: str, path: Path | None = None) -> dict:
    rec = {"command": cmd, "exit_code": int(code), "stdout": stdout, "stderr": stderr}
    if path is not None:
        text = "$ " + " ".join(cmd) + "\n[exit code: " + str(code) + "]\n" + stdout
        if stderr:
            text += "\n[stderr]\n" + stderr
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + ("\n" if not text.endswith("\n") else ""), encoding="utf-8")
        rec["raw_log"] = str(path)
    return rec

def run_capture(cmd: list[str], cwd: Path, log_path: Path, timeout: int = 30) -> dict:
    try:
        proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
        return command_record(cmd, proc.returncode, proc.stdout, proc.stderr, log_path)
    except subprocess.TimeoutExpired as exc:
        return command_record(cmd, 124, str(exc.stdout or ""), str(exc.stderr or "timeout"), log_path)
    except Exception as exc:
        return command_record(cmd, 127, "", str(exc), log_path)

def output_fields(case: dict) -> list[str]:
    body = case.get("output_contract")
    if isinstance(body, dict):
        fields = [str(k) for k in body]
    elif isinstance(body, list):
        fields = [str(k) for k in body]
    else:
        fields = []
    for key in ("case_id", "deliverable", "preserved_facts", "evidence_map", "change_log"):
        if key not in fields:
            fields.append(key)
    return fields

def initial_result(case_path: Path, case: dict, output: Path) -> dict:
    case_id = case.get("case_id")
    missing_id = not isinstance(case_id, str) or not case_id.strip()
    case_id = case_id.strip() if isinstance(case_id, str) and case_id.strip() else "not_reported"
    draft = output / "draft.md"
    output.mkdir(parents=True, exist_ok=True)
    if not draft.exists():
        draft.write_text("# Evidence-bound writing result\n\nStatus: partial. Material inspection has just started.\n",
                         encoding="utf-8")
    limits = [{"status": "partial", "reason": "The safety result was written before material extraction."}]
    if missing_id:
        limits.append({"status": "input_error", "reason": "case_id is absent or not a non-empty string; no id was inferred."})
    result = {
        "case_id": case_id,
        "deliverable": {
            "path": str(draft), "format": "markdown", "editable": True,
            "structure": ["Evidence status", "Claim-evidence ledger", "Limitations and human handoff"],
            "previews": nr("Rendering has not run yet.")
        },
        "preserved_facts": [{"fact": "scientific facts", **nr("Material has not been inspected yet.", str(case_path)),
                              "changed": False}],
        "evidence_map": [{"claim_id": "C-NR-BOOT", "claim": "not_reported",
                          **nr("No source mapping exists before extraction.", str(case_path)),
                           "verification": "not_reported"}],
        "change_log": [{"action": "initialize_safety_result", "target": str(output/"result.json"),
                        "reason": "Preserve an honest editable result before any expensive parsing.",
                        "human_check": 0}],
        "status": "input_error" if missing_id else "partial",
        "limitations": limits,
        "uncertainties": [{"kind": "material_availability", "value": "not_reported",
                           "reason": "Availability will be established from actual files and embedded material."}],
        "sources": [{"path": str(case_path), "role": "case_contract", "research_content": False}],
        "warnings": [{"code": "bootstrap_pending", "reason": "Extraction and validation have not finished."}],
        "output_contract":{"source":str(case_path),"fields":output_fields(case)},
        "runtime": {"started_at": now_iso(), "network_access": "not_used"}
    }
    for key in output_fields(case):
        if key not in result:
            result[key] = nr("This case-specific field awaits evidence-backed authoring.",
                             str(case_path) + ":output_contract/" + key)
    return result

def save_result(result: dict, output: Path, work: Path) -> None:
    target = output / "result.json"
    if target.exists():
        try:
            shutil.copy2(target, work / "result.prev.json")
        except OSError:
            pass
    write_json(target, result)

def declared_paths(value, key: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            found.extend(declared_paths(v, str(k)))
    elif isinstance(value, list):
        for item in value:
            found.extend(declared_paths(item, key))
    elif isinstance(value, str) and "\n" not in value and len(value) < 1000:
        s = value.strip()
        if Path(s).suffix.lower() in SUPPORTED_SUFFIXES | {".json"}:
            if key.lower() in {"path", "file", "filepath", "file_path", "pdf", "document", "material",
                               "manuscript", "source", "input", "draft", "attachment"} or "/" in s or s.startswith("."):
                found.append(s)
    return found

def resolve_materials(case_path: Path, case: dict, input_root: Path, work: Path) -> tuple[list[Path], list[dict]]:
    candidates: list[Path] = []
    warnings: list[dict] = []
    roots = [case_path.parent, input_root, work]
    for raw in declared_paths(case):
        p = Path(raw).expanduser()
        choices = [p] if p.is_absolute() else [root / p for root in roots]
        chosen = next((q for q in choices if q.is_file()), None)
        if chosen is not None:
            candidates.append(chosen.resolve())
        else:
            warnings.append({"code": "declared_file_unavailable", "path": raw,
                             "reason": "The case declares this path, but no readable file was resolved."})
    if input_root.is_dir():
        for p in sorted(input_root.rglob("*")):
            try:
                if not p.is_file() or p.resolve() == case_path.resolve():
                    continue
            except OSError:
                continue
            if p.name.lower() in {"task.md", "output-contract.md", "resources.yaml"}:
                continue
            if p.suffix.lower() in SUPPORTED_SUFFIXES and ".json" != p.suffix.lower():
                candidates.append(p.resolve())
    unique: list[Path] = []
    seen = set()
    for path in candidates:
        token = str(path)
        if token in seen:
            continue
        seen.add(token)
        unique.append(path)
    if len(unique) > 40:
        warnings.append({"code": "material_file_limit", "reason": "Only the first 40 resolved material files were indexed.",
                         "omitted": len(unique) - 40})
        unique = unique[:40]
    return unique, warnings

def embedded_material(case: dict) -> list[dict]:
    out: list[dict] = []

    def visit(value, pointer: str, in_material: bool = False) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                loc = pointer + "/" + str(key)
                marked = in_material or str(key) in MATERIAL_CONTAINERS
                if str(key) in CONTENT_KEYS and isinstance(item, str) and len(item.strip()) >= 20:
                    out.append({"pointer": loc, "text": item, "only_abstract": False})
                else:
                    visit(item, loc, marked)
        elif isinstance(value, list):
            for i, item in enumerate(value):
                visit(item, pointer + "/" + str(i), in_material)
        elif isinstance(value, str) and in_material and len(value.strip()) >= 40:
            out.append({"pointer": pointer, "text": value, "only_abstract": False})

    for key in MATERIAL_CONTAINERS:
        if key in case:
            visit(case[key], "/" + key, True)
    for key in ("abstract", "source_abstract"):
        if isinstance(case.get(key), str) and len(case[key].strip()) >= 20:
            out.append({"pointer": "/" + key, "text": case[key], "only_abstract": True})
    unique = []
    seen = set()
    for item in out:
        token = (item["pointer"], item["text"])
        if token in seen:
            continue
        seen.add(token)
        unique.append(item)
    return unique

def text_blocks(text: str, source_id: str, source_path: str, role: str = "text") -> list[dict]:
    lines = text.splitlines()
    blocks: list[dict] = []
    start: int | None = None
    buf: list[str] = []

    def flush() -> None:
        nonlocal start, buf
        if start is None or not any(x.strip() for x in buf):
            start, buf = None, []
            return
        end = start + len(buf) - 1
        locator = f"{source_id}:l{start}" if start == end else f"{source_id}:l{start}-l{end}"
        blocks.append({"id": locator, "locator": locator, "source_id": source_id,
                       "source_path": source_path, "page": None, "type": role,
                       "text": "\n".join(buf).strip()})
        start, buf = None, []

    for i, line in enumerate(lines, start=1):
        if not line.strip():
            flush()
            continue
        if start is None:
            start = i
        buf.append(line)
        if sum(len(x) for x in buf) >= 2500:
            flush()
    flush()
    if not lines and text.strip():
        return [{"id": source_id + ":l1", "locator": source_id + ":l1", "source_id": source_id,
                 "source_path": source_path, "page": None, "type": role, "text": text.strip()}]
    return blocks

def office_blocks(path: Path, source_id: str) -> tuple[list[dict], list[str]]:
    warnings: list[str] = []
    texts: list[str] = []
    try:
        with zipfile.ZipFile(path) as zf:
            if path.suffix.lower() == ".docx":
                names = ["word/document.xml"]
            else:
                names = sorted((n for n in zf.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                               key=lambda n: int(re.search(r"(\d+)", Path(n).stem).group(1)))
            for name in names:
                root = ET.fromstring(zf.read(name))
                chunks = [el.text or "" for el in root.iter() if el.tag.endswith("}t")]
                text = " ".join(chunks).strip()
                if text:
                    texts.append(text)
    except Exception as exc:
        warnings.append(f"OOXML text extraction failed: {str(exc)[:160]}")
    blocks: list[dict] = []
    for i, text in enumerate(texts, start=1):
        locator = f"{source_id}:part{i}"
        blocks.append({"id": locator, "locator": locator, "source_id": source_id,
                       "source_path": str(path), "page": i if path.suffix.lower() == ".pptx" else None,
                       "type": "body", "text": text})
    return blocks, warnings

def extract_all(paths: list[Path], embedded: list[dict], work: Path, deadline: float,
                reuse: bool = False) -> tuple[list[dict], list[dict], list[dict]]:
    cached = work / "blocks.json"
    if reuse and cached.is_file():
        try:
            data = json.loads(cached.read_text(encoding="utf-8"))
            if data.get("blocks") is not None:
                return data["blocks"], data.get("sources") or [], data.get("warnings") or []
        except Exception:
            pass
    blocks: list[dict] = []
    sources: list[dict] = []
    warnings: list[dict] = []
    work.joinpath("extracted").mkdir(parents=True, exist_ok=True)
    ordinal = 0
    for item in embedded:
        ordinal += 1
        sid = f"S{ordinal:02d}"
        pointer = item["pointer"]
        virtual = work / "extracted" / f"{sid}.txt"
        virtual.write_text(item["text"], encoding="utf-8")
        part = text_blocks(item["text"], sid, str(case_pointer(path=Path("case.json"), pointer=pointer)))
        for b in part:
            b["case_pointer"] = pointer
            if item.get("only_abstract"):
                b["type"] = "abstract"
        blocks.extend(part)
        sources.append({"source_id": sid, "path": str(virtual), "case_pointer": pointer,
                        "role": "embedded_abstract" if item.get("only_abstract") else "embedded_material",
                        "sha256": sha256(virtual), "blocks": len(part)})
        if item.get("only_abstract"):
            warnings.append({"code": "abstract_only", "source_id": sid,
                             "reason": "Only an abstract was supplied for this item; Methods and full-text support remain unavailable."})
    for path in paths:
        if time.monotonic() >= deadline:
            warnings.append({"code": "index_deadline", "path": str(path),
                             "reason": "Indexing stopped at the internal deadline; later files were not inspected."})
            break
        ordinal += 1
        sid = f"S{ordinal:02d}"
        role = "material"
        rec = {"source_id": sid, "path": str(path), "role": role, "sha256": sha256(path),
               "format": path.suffix.lower().lstrip(".")}
        try:
            if path.suffix.lower() == ".pdf":
                import pdf_text
                data = pdf_text.extract(str(path))
                part = []
                for b in data.get("blocks") or []:
                    original = b.get("id") or b.get("locator") or "block"
                    loc = f"{sid}:{original}"
                    part.append({"id": loc, "locator": loc, "source_id": sid, "source_path": str(path),
                                 "page": b.get("page"), "type": b.get("type") or "body",
                                 "section": b.get("section"), "bbox": b.get("bbox"),
                                 "text": (b.get("text") or "").strip()})
                rec.update({"extraction_engine": data.get("engine"), "page_count": data.get("page_count"),
                            "blocks": len(part)})
                for message in data.get("engine_errors") or []:
                    warnings.append({"code": "pdf_engine_fallback", "source_id": sid, "reason": message})
                for message in data.get("warnings") or []:
                    warnings.append({"code": "pdf_extraction_warning", "source_id": sid, "reason": message})
                if data.get("page_count") and not part:
                    warnings.append({"code": "no_text_layer", "source_id": sid, "path": str(path),
                                     "reason": f"{data.get('page_count')} page(s) yielded no text blocks; OCR or manual transcription is required."})
                write_json(work / "extracted" / f"{sid}.blocks.json", data)
            elif path.suffix.lower() in OFFICE_SUFFIXES:
                part, msgs = office_blocks(path, sid)
                rec["blocks"] = len(part)
                warnings.extend({"code": "office_extraction_warning", "source_id": sid, "reason": m} for m in msgs)
            else:
                raw = path.read_bytes()
                if len(raw) > 2_000_000:
                    raw = raw[:2_000_000]
                    warnings.append({"code": "material_truncated", "source_id": sid,
                                     "reason": "Only first 2MB indexed; inspect remainder manually."})
                try:
                    text = raw.decode("utf-8")
                    rec["decode"] = "utf-8"
                except UnicodeDecodeError:
                    text = raw.decode("utf-8", "replace")
                    rec["decode"] = "utf-8-replace"
                    warnings.append({"code": "decode_replacement", "source_id": sid,
                                     "reason": "Invalid UTF-8 replaced; manual inspection required."})
                part = text_blocks(text, sid, str(path))
                rec["blocks"] = len(part)
            blocks.extend([b for b in part if (b.get("text") or "").strip()])
            if not part:
                warnings.append({"code": "no_readable_text", "source_id": sid, "path": str(path),
                                 "reason": "No research text could be extracted from this material."})
        except Exception as exc:
            rec["read_error"] = str(exc)[:300]
            warnings.append({"code": "material_read_failed", "source_id": sid, "path": str(path),
                             "reason": str(exc)[:300]})
        sources.append(rec)
    material = "\n\n".join(f"[{b['locator']}] {b['text']}" for b in blocks)
    (work / "material.txt").write_text(material, encoding="utf-8")
    write_json(cached, {"blocks": blocks, "sources":sources, "warnings": warnings})
    with (work / "blocks.jsonl").open("w", encoding="utf-8") as fh:
        for b in blocks:
            fh.write(json.dumps(b, ensure_ascii=False) + "\n")
    return blocks, sources, warnings

def case_pointer(path: Path, pointer: str) -> str:
    return str(path) + "#" + pointer

def choose_claims(blocks: list[dict], limit: int = 12) -> list[dict]:
    candidates = []
    for order, block in enumerate(blocks):
        if block.get("type") in {"furniture", "heading", "reference", "footnote"}:
            continue
        text = " ".join((block.get("text") or "").split())
        if len(text) < 35:
            continue
        pieces = [s.strip() for s in re.split(r"(?<=[.!?。！？])\s+", text) if len(s.strip()) >= 35]
        if not pieces and len(text) >= 35:
            pieces = [text]
        for sentence in pieces:
            if len(sentence) > 1200:
                continue
            score = 0
            if evidence.NUMBER_RE.search(sentence):
                score += 3
            if evidence.COMPARATOR_RE.search(sentence):
                score += 2
            if re.search(r"\b(results?|discussion|conclusion|abstract)\b|结果|讨论|结论|摘要",
                         str(block.get("section") or block.get("type") or ""), re.I):
                score += 2
            if re.search(r"\b(doi|keywords|copyright|received|accepted)\b", sentence, re.I):
                score -= 4
            candidates.append({"score": score, "order": order, "block": block, "text": sentence})
    candidates.sort(key=lambda c: (-c["score"], c["order"]))
    selected = candidates[:limit]
    selected.sort(key=lambda c: c["order"])
    return selected

def make_claims(selected: list[dict]) -> list[dict]:
    rows = []
    for i, item in enumerate(selected, start=1):
        sentence = item["text"]
        b = item["block"]
        rows.append({
            "claim_id": f"C{i:03d}",
            "claim": f"Source {b['source_id']} states: {sentence}",
            "locator": b["locator"], "source_path": b["source_path"], "quote": sentence,
            "exact": True, "verdict": "supported_attribution",
            "evidence_strength": "not_reported",
            "strength_reason": "Verbatim attribution is verified, but study design, replication and certainty require semantic review before assigning scientific evidence strength.",
            "scope": "Only the population, intervention, comparator and conditions stated in the quotation.",
            "support_type": "verbatim_source_attribution"
        })
    return rows

def numeric_facts(blocks: list[dict], limit: int = 80) -> tuple[list[dict], int]:
    facts = []
    seen = set()
    total = 0
    for b in blocks:
        text = evidence.normalize(b.get("text") or "")
        for match in evidence.NUMBER_RE.finditer(text):
            total += 1
            if len(facts) >= limit:
                continue
            value = match.group(0).strip()
            key = (b["locator"], value)
            if key in seen:
                continue
            seen.add(key)
            left = max(0, match.start() - 100)
            right = min(len(text), match.end() + 100)
            facts.append({"fact": "numeric_or_unit_token", "value": value, "source": b["locator"],
                          "source_path": b["source_path"], "quote": text[left:right].strip(),
                          "changed": False, "verification": "extracted_without_unit_conversion"})
    return facts, total

def auto_authoring(case: dict, claims: list[dict], facts: list[dict], no_material_reason: str | None) -> dict:
    lines: list[str] = ["# Evidence-bound writing result", ""]
    if no_material_reason:
        lines += ["## Evidence status", "",
                  "`insufficient_evidence`: no readable source material.",
                  "",
                  "Claims, numbers, evidence strength and references are `not_reported` because source material is absent.",
                  "",
                  "## Claim–evidence ledger", "",
                  "| Claim | Value | Reason |", "|---|---|---|",
                  "| C-NR-001 | not_reported | No assertion supported. |", ""]
        status = "insufficient_evidence"
        limitations = [{"kind": "missing_material", "value": "not_reported", "reason": no_material_reason,
                        "human_action": "Provide source material and rerun."}]
    else:
        status = "partial"
        lines += ["## Scope", "",
                  "Extractive source-attributed draft. Evidence strength is `not_reported` pending review.",
                  "",
                  "## Claim–evidence ledger", ""]
        for row in claims:
            lines += [f"### {row['claim_id']}", "",
                      f"{row['claim']} [{row['locator']}]", "",
                      f"- Quote: “{row['quote']}” [{row['locator']}]",
                      f"- Strength: `not_reported` — {row['strength_reason']}",
                      f"- Scope: {row['scope']} [{row['locator']}]", ""]
        limitations = [{"kind": "authoring_pending", "value": "not_reported",
                        "reason": "Draft preserves attribution; genre/format pending.",
                        "human_action": "Author prose and bind claims in authoring.json."}]
    lines += ["## Limitations", ""]
    for item in limitations:
        lines += [f"- {item['reason']}", f"- Action: {item['human_action']}"]
    return {"title": "Evidence-bound writing result", "draft_markdown": "\n".join(lines).rstrip() + "\n",
            "requested_status": status, "limitations": limitations}

def markdown_to_spec(markdown: str, title: str, font: str) -> dict:
    blocks: list[dict] = []
    paragraph: list[str] = []

    def flush() -> None:
        nonlocal paragraph
        if paragraph:
            blocks.append({"type": "p", "text": " ".join(x.strip() for x in paragraph)})
            paragraph = []

    lines = markdown.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if re.match(r"^#{1,3}\s+", line):
            flush()
            m = re.match(r"^(#{1,3})\s+(.*)$", line)
            blocks.append({"type": "h1" if len(m.group(1)) <= 2 else "h2", "text": m.group(2).strip()})
        elif line.strip().startswith("|") and line.strip().endswith("|"):
            flush()
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|") and lines[i].strip().endswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{3,}:?", c.replace(" ", "")) for c in cells):
                    rows.append(cells)
                i += 1
            i -= 1
            if rows:
                blocks.append({"type": "table", "rows": rows})
        elif re.match(r"^\s*[-*+]\s+", line):
            flush()
            blocks.append({"type": "bullet", "text": re.sub(r"^\s*[-*+]\s+", "", line)})
        elif not line.strip():
            flush()
        else:
            paragraph.append(line)
        i += 1
    flush()
    return {"title": title, "font": font, "required_font": font, "blocks": blocks}

def requested_font(case: dict) -> str:
    blob = json.dumps(case, ensure_ascii=False)
    lowered = blob.lower()
    for name in FONT_NAMES:
        if name.lower() in lowered:
            return name
    return "Liberation Serif"

def inspect_previews(pdf: str | None, pngs: list[str], docx: Path | None = None) -> dict:
    report = {"png": [], "pdf": nr("No PDF preview was reported."), "docx": nr("No DOCX source was generated.")}
    for raw in pngs:
        p = Path(raw)
        item = {"path": str(p), "exists": p.is_file()}
        if p.is_file():
            data = p.read_bytes()[:32]
            item["png_signature"] = data.startswith(b"\x89PNG\r\n\x1a\n")
            if item["png_signature"] and len(data) >= 24:
                item["width"] = int.from_bytes(data[16:20], "big")
                item["height"] = int.from_bytes(data[20:24], "big")
            item["bytes"] = p.stat().st_size
        report["png"].append(item)
    if pdf:
        p = Path(pdf)
        item = {"path": str(p), "exists": p.is_file()}
        if p.is_file():
            item["pdf_signature"] = p.read_bytes()[:5] == b"%PDF-"
            item["bytes"] = p.stat().st_size
            try:
                import fitz
                with fitz.open(p) as doc:
                    item["page_count"] = len(doc)
                    item["text_characters"] = sum(len(page.get_text()) for page in doc)
            except Exception as exc:
                item["page_audit"] = "not_reported"
                item["page_audit_reason"] = str(exc)[:160]
        report["pdf"] = item
    if docx is not None:
        item = {"path": str(docx), "exists": docx.is_file()}
        if docx.is_file():
            try:
                with zipfile.ZipFile(docx) as zf:
                    bad = zf.testzip()
                    root = ET.fromstring(zf.read("word/document.xml"))
                    text = "".join(el.text or "" for el in root.iter() if el.tag.endswith("}t"))
                    item.update({"zip_valid": bad is None, "document_xml_valid": True,
                                 "text_characters": len(text)})
            except Exception as exc:
                item.update({"zip_valid": False, "document_xml_valid": False, "reason": str(exc)[:200]})
        report["docx"] = item
    png_ok = bool(report["png"] and all(x.get("exists") and x.get("png_signature") and
                                        x.get("width", 0) > 0 and x.get("height", 0) > 0
                                        for x in report["png"]))
    pdf_ok = bool(isinstance(report["pdf"], dict) and report["pdf"].get("exists")
                  and report["pdf"].get("pdf_signature") and report["pdf"].get("page_count", 0) > 0)
    docx_ok = bool(report["docx"].get("exists") and report["docx"].get("zip_valid")
                   and report["docx"].get("document_xml_valid"))
    report["ok"] = png_ok and (pdf_ok or docx_ok)
    return report

def render_document(spec: dict, output: Path, work: Path, warnings: list[dict]) -> tuple[dict, dict]:
    import ooxml
    import preview
    import render
    docx = output / "draft.docx"
    build = ooxml.build_docx(spec, str(docx))
    native_dir = work / "native_preview"
    native = render.preview_bundle(str(docx), str(native_dir), dpi=120)
    if native.get("ok"):
        previews = native.get("previews") or []
        pdfs = sorted(native_dir.glob("*.pdf"))
        pdf = str(pdfs[0]) if pdfs else None
        for p in previews + [str(x) for x in pdfs]:
            target = output / "preview" / Path(p).name
            target.parent.mkdir(parents=True, exist_ok=True)
            if Path(p).resolve() != target.resolve():
                shutil.copy2(p, target)
        pngs = [str(output / "preview" / Path(p).name) for p in previews]
        pdf_out = str(output / "preview" / Path(pdf).name) if pdf else None
        engine = "office+pdf_rasteriser"
        fallback = {"status": "not_used", "reason": "Native Office rendering succeeded."}
    else:
        warnings.append({"code": "office_renderer_unavailable", "severity": "warning",
                         "reason": native.get("steps", [{}])[-1].get("office_to_pdf", {}).get("error",
                             "Native Office rendering did not produce a preview."),
                         "fallback": "Render the same document specification with matplotlib to PDF and PNG."})
        fallback = preview.doc_png(spec, str(output / "preview"))
        for warning in fallback.get("font", {}).get("warnings") or []:
            warnings.append(warning)
        pngs = fallback.get("pages") or []
        pdf_out = fallback.get("pdf")
        engine = fallback.get("engine") or "not_reported"
    audit = inspect_previews(pdf_out, pngs, docx)
    audit["engine"] = engine
    audit["native_office"] = native
    audit["fallback"] = fallback
    write_json(output / "preview" / "preview_report.json", audit)
    if not audit["ok"]:
        warnings.append({"code": "preview_incomplete", "severity": "error",
                         "reason": "The editable document or its real PDF/PNG preview did not pass the file audit; see preview_report.json."})
    info = {"docx": build, "pdf": pdf_out or nr("No valid PDF preview was produced."),
            "png": pngs or [nr("No valid PNG preview was produced.")], "engine": engine,
            "audit": str(output / "preview" / "preview_report.json"), "ok": audit["ok"]}
    return info, audit

def validate_authoring(authoring: dict, blocks: list[dict], material_path: Path) -> tuple[list[dict], list[dict], list[dict]]:
    maps = authoring.get("evidence_map")
    if not isinstance(maps, list) or not maps:
        return [], [], [{"code": "authoring_map_missing", "reason": "authoring.json must contain a non-empty evidence_map list."}]
    index = evidence.LocatorIndex({"blocks": blocks})
    verifications: list[dict] = []
    problems: list[dict] = []
    draft = str(authoring.get("draft_markdown") or "")
    for i, row in enumerate(maps, start=1):
        if not isinstance(row, dict):
            problems.append({"code": "map_shape", "index": i, "reason": "Each evidence_map item must be an object."})
            continue
        cid = str(row.get("claim_id") or f"C{i:03d}")
        quote = str(row.get("quote") or "")
        verdict = evidence.verify_quote(index, quote)
        verdict["claim_id"] = cid
        supplied_locator = str(row.get("locator") or "")
        verdict["supplied_locator"] = supplied_locator
        verdict["locator_matches"] = verdict.get("locator") == supplied_locator
        verdict["claim_anchor_present"] = cid in draft
        verdict["strength_present"] = bool(str(row.get("evidence_strength") or "").strip()) and row.get("evidence_strength") != "not_reported"
        if not verdict.get("exact"):
            problems.append({"code": "quote_not_exact", "claim_id": cid, "reason": "The quote was not found verbatim in the indexed source."})
        if not verdict["locator_matches"]:
            problems.append({"code": "locator_mismatch", "claim_id": cid,
                             "reason": f"Expected {verdict.get('locator')!r}, received {supplied_locator!r}."})
        if not verdict["claim_anchor_present"]:
            problems.append({"code": "claim_anchor_missing", "claim_id": cid,
                             "reason": "The claim_id does not occur in draft_markdown, so a reviewer cannot bind the prose to this row."})
        if not verdict["strength_present"]:
            problems.append({"code": "strength_missing", "claim_id": cid,
                             "reason": "An answered result needs a case-specific evidence_strength and strength_reason."})
        verifications.append(verdict)
    facts = authoring.get("preserved_facts")
    if not isinstance(facts, list) or not facts:
        facts = [{"fact": "author_supplied_facts", **nr("No preserved_facts supplied; auto-extraction is authoritative.", str(material_path)),
                  "changed": False}]
    return maps, facts, problems

def run(args: argparse.Namespace) -> int:
    started = time.monotonic()
    started_at = now_iso()
    case_path = Path(args.case).expanduser().resolve()
    work = Path(args.work).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    input_root = Path(args.input_root).expanduser().resolve() if args.input_root else case_path.parent
    work.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    logs = output / "validation"
    logs.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    try:
        case = json.loads(case_path.read_text(encoding="utf-8"))
        if not isinstance(case, dict):
            raise ValueError("case.json must contain a JSON object")
    except Exception as exc:
        case = {"case_id": "not_reported", "output_contract": {}}
        result = initial_result(case_path, case, output)
        result.update({"status": "input_error",
                       "limitations": [{"kind": "invalid_case", "reason": str(exc)[:300],
                                        "human_action": "Supply a readable UTF-8 JSON object at the case path."}]})
        save_result(result, output, work)
        print(json.dumps({"case_id": "not_reported", "status": "input_error", "error": str(exc)[:300]}, ensure_ascii=False))
        return 2
    result = initial_result(case_path, case, output)
    save_result(result, output, work)
    deadline = started + max(5, args.max_seconds - 15)
    paths, path_warnings = resolve_materials(case_path, case, input_root, work)
    embedded = embedded_material(case)
    blocks, sources, warnings = extract_all(paths, embedded, work, deadline, reuse=args.reuse_index)
    warnings = path_warnings + warnings
    sources = [{"source_id": "CASE", "path": str(case_path), "role": "case_contract", "research_content": False,
                "sha256": sha256(case_path)}] + sources
    selected = choose_claims(blocks)
    claims = make_claims(selected)
    facts, total_numbers = numeric_facts(blocks)
    auto = auto_authoring(case, claims, facts, None if blocks else
                          "No readable research text found in case or materials.")
    authoring: dict = dict(auto)
    authoring_problems: list[dict] = []
    verifications: list[dict] = []
    if args.authoring:
        authoring_path = Path(args.authoring).expanduser().resolve()
        supplied = json.loads(authoring_path.read_text(encoding="utf-8"))
        if not isinstance(supplied, dict):
            raise ValueError("authoring JSON must be an object")
        merged = dict(auto)
        merged.update(supplied)
        authoring = merged
        if blocks:
            supplied_maps, supplied_facts, authoring_problems = validate_authoring(supplied, blocks, work / "material.txt")
            if supplied_maps:
                claims = supplied_maps
            facts = supplied_facts
            verifications = supplied.get("_verifications") or []
            verifications = []
            index = evidence.LocatorIndex({"blocks": blocks})
            for row in supplied_maps:
                item = evidence.verify_quote(index, str(row.get("quote") or ""))
                item["claim_id"] = row.get("claim_id")
                item["supplied_locator"] = row.get("locator")
                item["locator_matches"] = row.get("locator") == item.get("locator")
                verifications.append(item)
                row["verdict"] = "verified" if item.get("exact") else "not_verified"
        else:
            authoring_problems.append({"code": "no_material_for_authoring", "reason": "Authoring requires readable source material."})
    if not claims:
        claims = [{"claim_id": "C-NR-001", "claim": "not_reported",
                   "locator": str(case_path) + ":/material", "quote": "not_reported",
                   "verification": "not_reported", "exact": False,
                   "verdict": "insufficient_evidence", "evidence_strength": "not_reported",
                   "strength_reason": auto["limitations"][0]["reason"]}]
    if not facts:
        facts = [{"fact": "numerical_findings", "value": "not_reported",
                  "source": str(case_path) + ":/material", "changed": False,
                  "reason": "No numeric finding in readable material."}]
    font = requested_font(case)
    spec = markdown_to_spec(str(authoring.get("draft_markdown") or auto["draft_markdown"]),
                            str(authoring.get("title") or "Evidence-bound writing result"), font)
    write_json(work / "writing_spec.json", spec)
    (output / "draft.md").write_text(str(authoring.get("draft_markdown") or auto["draft_markdown"]), encoding="utf-8")
    preview_info, preview_audit = render_document(spec, output, work, warnings)
    selected_text = "\n\n".join(f"{row['claim']} [{row['locator']}]\n" for row in claims if row.get("exact")) or "not_reported\n"
    (work / "selected_claims.txt").write_text(selected_text, encoding="utf-8")
    with (work / "claims_plan.jsonl").open("w", encoding="utf-8") as fh:
        for row in claims:
            fh.write(json.dumps({"claim_id": row.get("claim_id"), "claim": row.get("claim"),
                                 "required_evidence": "verbatim quote plus source locator",
                                 "locator": row.get("locator")}, ensure_ascii=False) + "\n")
    quotes_path = work / "quotes.json"
    write_json(quotes_path, [{"id": row.get("claim_id"), "quote": row.get("quote")} for row in claims if row.get("exact")])
    quote_log = logs / "evidence_quotes.json"
    if any(row.get("exact") for row in claims):
        rec = run_capture([sys.executable, str(HERE / "evidence.py"), "verify", str(work/"blocks.json"), str(quotes_path)],
                          work, quote_log)
        records.append(rec)
        if not args.authoring:
            try:
                verifications = json.loads(rec["stdout"])
            except Exception:
                verifications = [{"status": "not_reported", "reason": "Evidence verifier output was not valid JSON."}]
    else:
        payload = {"status": "not_reported", "reason": "No quotation to verify; no readable material indexed.",
                   "verification": []}
        write_json(quote_log, payload)
        records.append({"command": ["evidence.py", "verify"], "exit_code": "not_run", "reason": payload["reason"],
                        "raw_log": str(quote_log)})
    contract_cmd = [sys.executable, str(HERE / "contract.py"), "check", str(output/"result.json"),
                    "--family", "writing", "--contract", str(case_path)]
    citations_cmd = [sys.executable, str(HERE / "text_checks.py"), "citations", str(output/"draft.md")]
    claims_cmd = [sys.executable, str(HERE / "text_checks.py"), "claims", str(output/"draft.md")]
    numbers_origin = work / ("material.txt" if args.authoring else "selected_claims.txt")
    numbers_cmd = [sys.executable, str(HERE / "text_checks.py"), "numbers", str(numbers_origin), str(output/"draft.md")]
    rec_citations = run_capture(citations_cmd, work, logs / "citations.log")
    rec_claims = run_capture(claims_cmd, work, logs / "claims.log")
    rec_numbers = run_capture(numbers_cmd, work, logs / "numbers.log")
    records.extend([rec_citations, rec_claims, rec_numbers])
    requested_status = str(authoring.get("requested_status") or authoring.get("status") or auto["requested_status"])
    if requested_status not in {"answered", "partial", "insufficient_evidence", "input_error"}:
        requested_status = "partial"
        warnings.append({"code": "invalid_requested_status", "reason": "Invalid status replaced with partial."})
    if not blocks:
        status = "insufficient_evidence"
    elif requested_status == "answered" and not args.authoring:
        status = "partial"
        warnings.append({"code": "answered_requires_authoring", "reason": "Auto-draft cannot be answered."})
    else:
        status = requested_status
    if authoring_problems and status == "answered":
        status = "partial"
        warnings.append({"code": "answered_downgraded", "reason": "Claim-binding gate failed."})
    try:
        number_data = json.loads(rec_numbers["stdout"])
    except Exception:
        number_data = {}
    added_numbers = number_data.get("numbers_added_in_b") or {}
    if added_numbers and status == "answered":
        status = "partial"
        warnings.append({"code": "unmatched_numbers", "reason": "Draft contains unmatched numeric tokens.",
                         "values": added_numbers})
    if rec_citations["exit_code"] != 0 and status == "answered":
        status = "partial"
        warnings.append({"code": "citation_gate_failed", "reason": "Citation check failed."})
    if rec_claims["exit_code"] != 0 and status == "answered":
        status = "partial"
        warnings.append({"code": "claim_marker_gate_failed", "reason": "Claim marker check failed."})
    # --- Finalize result via runner module ---
    from runner import finalize_result
    finalize_result(args, case, case_path, output, work, spec, preview_info, claims, facts, sources, warnings,
                    verifications, authoring_problems, records, started, started_at, auto, authoring, contract_cmd,
                    status, total_numbers, preview_audit, logs, sha256, now_iso, nr, output_fields, save_result,
                    run_capture, write_json)
    return 0

def main() -> int:
    parser = argparse.ArgumentParser(description="Offline evidence-bound case runner")
    parser.add_argument("--case", default="/input/case.json")
    parser.add_argument("--input-root", default=None, help="Material root; defaults to the case file directory")
    parser.add_argument("--work", default="/app/work")
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--authoring", default=None, help="Optional evidence-backed authoring JSON")
    parser.add_argument("--reuse-index", action="store_true", help="Reuse work/blocks.json during finalization")
    parser.add_argument("--max-seconds", type=int, default=105, help="Internal soft budget; result is saved before this limit")
    args = parser.parse_args()
    return run(args)

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        traceback.print_exc()
        print(json.dumps({"status": "input_error", "error": str(exc)[:300]}, ensure_ascii=False), file=sys.stderr)
        sys.exit(3)
