#!/usr/bin/env python3
"""Execute the supplied-scope scientific figure reading workflow.

The program never guesses scientific labels, tick values, units, or data.  It
inventories /input/case.json, extracts literal source tables, probes supplied
raster images, writes an early contract-complete result, and optionally merges
an evidence-linked visual review prepared by the calling Agent.

Typical two-pass use (the second pass is needed only when materials exist):
  python3 run_case.py /input/case.json --work-dir /app/work -o /app/output/result.json
  # inspect /app/work/visual_review.template.json and the actual image/material
  python3 run_case.py /input/case.json --work-dir /app/work \
      --review /app/work/visual_review.json -o /app/output/result.json

Only local files are read.  No network or model API is used by this script.
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
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterable

VERSION = "3.0.0"
DEFAULT_CONTRACT = {
    "case_id": "原样保留",
    "status": "answered|partial|insufficient_evidence|input_error",
    "result": "题目主结果，结构化字段按题目任务卡",
    "claims": "每条关键结论附 kind、evidence、source_id、location",
    "uncertainties": "未报告、无法判断、冲突和人工复核项",
}
IMAGE_FIELDS = (
    "panel_labels", "caption", "axes", "axis_scales", "axis_ticks",
    "axis_labels_and_units", "legend", "series", "data_points",
    "error_bars", "significance_marks", "scale_bar", "image_features", "trends",
)
TABLE_FIELDS = (
    "caption", "headers", "grouping_and_conditions", "rows", "units",
    "footnotes", "uncertainty_or_statistics", "trends",
)
ID_KEYS = ("paragraph_id", "table_id", "block_id", "id", "source_id")


def nr(reason: str) -> dict[str, str]:
    return {"status": "not_reported", "reason": reason}


def has_task_answer(value: Any) -> bool:
    if value in (None, "", [], {}):
        return False
    if isinstance(value, dict) and value.get("status") in ("not_reported", "not_applicable"):
        return False
    if isinstance(value, str) and value.strip().lower() in ("not_reported", "unknown", "n/a"):
        return False
    return True


def nonempty_list(value: Any, reason: str) -> list[Any]:
    """Represent absent collections explicitly instead of returning an empty array."""
    if value is None or value == []:
        return [nr(reason)]
    return value if isinstance(value, list) else [value]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def unique_paths(paths: Iterable[Path | None]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        if path is None:
            continue
        try:
            key = str(path.expanduser().resolve())
        except (OSError, TypeError, ValueError):
            continue
        if key not in seen:
            seen.add(key)
            result.append(Path(key))
    return result


def find_declared_file(declared: str, case_dir: Path, work_dir: Path) -> Path | None:
    if not declared:
        return None
    raw = Path(declared).expanduser()
    if raw.is_absolute():
        return raw.resolve() if raw.is_file() else None
    bases = unique_paths([
        case_dir,
        case_dir.parent if case_dir else None,
        Path("/input"),
        Path("/app/input"),
        Path("/inputs"),
        Path("/app/inputs"),
        work_dir,
        Path("/app/work"),
        Path.cwd(),
    ])
    candidates: list[Path] = []
    for base in bases:
        candidates.extend((base / raw, base / raw.name, base / "artifacts" / raw.name,
                           base / "materials" / raw.name))
    for candidate in unique_paths(candidates):
        if candidate.is_file():
            return candidate.resolve()
    # Bounded basename search in input roots only; never search the whole filesystem.
    walk_roots = unique_paths([
        case_dir,
        case_dir.parent if case_dir else None,
        Path("/input"),
        Path("/app/input"),
        Path("/inputs"),
        Path("/app/inputs"),
        work_dir,
    ])
    for root in walk_roots:
        if not root.is_dir():
            continue
        visited = 0
        for base, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d != "__pycache__")
            for name in sorted(files):
                visited += 1
                if name == raw.name:
                    return (Path(base) / name).resolve()
                if visited >= 5000:
                    return None
    return None


def material_candidates(case: dict[str, Any], case_dir: Path, work_dir: Path) -> list[Path]:
    hints: list[str] = []
    for owner in (case, case.get("organizer_supplied_scope") or {}, case.get("source") or {}):
        if not isinstance(owner, dict):
            continue
        for key in ("material", "material_file", "material_path", "material_json"):
            value = owner.get(key)
            if isinstance(value, str) and value.lower().endswith(".json"):
                hints.append(value)
    candidates = [Path(hint) if Path(hint).is_absolute() else case_dir / hint for hint in hints]
    for base in (case_dir, case_dir.parent, Path("/input"), Path("/app/input"),
                 Path("/inputs"), Path("/app/inputs"), work_dir, Path("/app/work")):
        candidates.extend((base / "material.json", base / "artifacts" / "material.json",
                           base / "materials" / "material.json"))
    existing = [path for path in unique_paths(candidates) if path.is_file()]
    for root in unique_paths([case_dir, case_dir.parent, Path("/input"), Path("/app/input"),
                              Path("/inputs"), Path("/app/inputs")]):
        if not root.is_dir():
            continue
        visited = 0
        for base, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d != "__pycache__")
            for name in sorted(files):
                visited += 1
                if name == "material.json":
                    path = (Path(base) / name).resolve()
                    if path not in existing:
                        existing.append(path)
                if visited >= 5000:
                    break
    required = (case.get("organizer_supplied_scope") or {}).get("required_literal_source_table_ids") or []
    if len(existing) > 1 and required:
        def score(path: Path) -> int:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return -1
            return sum(str(table_id) in text for table_id in required)
        existing.sort(key=score, reverse=True)
    return existing


def pointer_token(value: Any) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


def find_object_by_id(data: Any, wanted: str) -> tuple[Any, str] | None:
    """Find a literal table by dictionary key or common source identifier field."""
    stack: list[tuple[Any, str]] = [(data, "")]
    visited = 0
    while stack and visited < 100000:
        value, pointer = stack.pop()
        visited += 1
        if isinstance(value, dict):
            for key, item in value.items():
                child = pointer + "/" + pointer_token(key)
                if str(key) == wanted:
                    return item, child
                if isinstance(item, dict) and any(str(item.get(k)) == wanted for k in ID_KEYS if k in item):
                    return item, child
                if isinstance(item, (dict, list)):
                    stack.append((item, child))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, dict) and any(str(item.get(k)) == wanted for k in ID_KEYS if k in item):
                    return item, pointer + "/" + str(index)
                if isinstance(item, (dict, list)):
                    stack.append((item, pointer + "/" + str(index)))
    return None


def markdown_grid(text: str) -> tuple[list[str], list[list[str]]]:
    lines = [line.strip() for line in text.splitlines() if line.strip().startswith("|")]
    if len(lines) < 2:
        return [], []
    cells = [[cell.strip() for cell in line.strip("|").split("|")] for line in lines]
    delimiter = lambda row: all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in row)
    if delimiter(cells[1]):
        return cells[0], cells[2:]
    return cells[0], cells[1:]


def normalize_table(payload: Any) -> dict[str, Any]:
    """Preserve the source payload and expose a literal grid when one is present."""
    headers: Any = None
    rows: Any = None
    text = payload if isinstance(payload, str) else ""
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        for key in ("headers", "column_headers", "columns"):
            if isinstance(payload.get(key), list):
                headers = payload[key]
                break
        for key in ("rows", "records", "data", "values"):
            if isinstance(payload.get(key), list):
                rows = payload[key]
                break
        for key in ("markdown", "text", "content", "raw_text"):
            if isinstance(payload.get(key), str):
                text = payload[key]
                break
        if isinstance(payload.get("table"), (dict, list, str)):
            nested = normalize_table(payload["table"])
            headers = headers if headers is not None else nested.get("headers_value")
            rows = rows if rows is not None else nested.get("rows_value")
    if rows is None and text:
        parsed_headers, parsed_rows = markdown_grid(text)
        if parsed_headers:
            headers, rows = parsed_headers, parsed_rows
    if headers is None and isinstance(rows, list) and rows and all(isinstance(row, dict) for row in rows):
        headers = list(dict.fromkeys(str(key) for row in rows for key in row))
    return {
        "headers_value": headers,
        "rows_value": rows,
        "headers": headers if headers else nr("源对象中未取得逐字表头；需查看 literal_source 并人工核对。"),
        "rows": rows if rows else [nr("源对象中未取得表格行；空值不代表原表没有数据。")],
    }


def source_line(path: Path, needle: str) -> tuple[str, str]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return path.name + ":not_reported", nr("无法读取源文件以取得逐字短引文。")  # type: ignore[return-value]
    for number, line in enumerate(lines, 1):
        if needle in line:
            return f"{path.name}:{number}", line.strip()
    return path.name + ":not_reported", needle


def claim(kind: str, text: str, source: Path, locator: str, quote: Any,
          paragraph_id: str | None = None, conditions: Any = None) -> dict[str, Any]:
    evidence = {"locator": locator, "quote": quote if isinstance(quote, str) else
                json.dumps(quote, ensure_ascii=False)[:1200]}
    record = {
        "kind": kind,
        "text": text,
        "evidence": evidence,
        "source_id": str(source),
        "location": locator,
        "conditions": conditions if conditions is not None else nr("无额外条件；仅适用于当前定位符指向的材料。"),
    }
    if paragraph_id:
        record["paragraph_id"] = paragraph_id
    return record


def image_declarations(case: dict[str, Any]) -> list[dict[str, Any]]:
    scope = case.get("organizer_supplied_scope") or {}
    raw = scope.get("required_actual_image_files") or case.get("images") or []
    output = []
    for index, item in enumerate(raw if isinstance(raw, list) else [raw]):
        if isinstance(item, str):
            item = {"file": item}
        if not isinstance(item, dict):
            continue
        declared = str(item.get("file") or item.get("path") or "")
        image_id = str(item.get("id") or item.get("image_id") or
                       (Path(declared).stem if declared else f"image_{index + 1}"))
        output.append({"id": image_id, "file": declared, "sha256": str(item.get("sha256") or "")})
    return output


def table_declarations(case: dict[str, Any]) -> list[str]:
    scope = case.get("organizer_supplied_scope") or {}
    raw = scope.get("required_literal_source_table_ids") or case.get("required_literal_source_table_ids") or []
    output = []
    for item in raw if isinstance(raw, list) else [raw]:
        if isinstance(item, dict):
            item = item.get("paragraph_id") or item.get("table_id") or item.get("id")
        if item:
            output.append(str(item))
    return output


def probe_image(path: Path, skill_dir: Path, work_dir: Path, image_id: str,
                deadline: float) -> dict[str, Any]:
    script = skill_dir / "scripts" / "chart_parse.py"
    if not script.is_file():
        return {"status": "error", "error": "chart_parse.py is missing"}
    remaining = deadline - time.monotonic()
    if remaining <= 1:
        return {"status": "timeout", "error": "全局时间余量不足，未启动图像探测。"}
    try:
        proc = subprocess.run(
            [sys.executable, str(script), "probe", str(path)], cwd=str(work_dir),
            capture_output=True, text=True, timeout=max(1, min(15, remaining - 1)),
        )
        output_path = work_dir / "probes" / (re.sub(r"[^A-Za-z0-9_.-]", "_", image_id) + ".json")
        if proc.stdout.strip().startswith("{"):
            payload = json.loads(proc.stdout)
            write_json(output_path, payload)
        if proc.returncode != 0:
            return {"status": "unreadable", "exit_code": proc.returncode,
                    "error": (proc.stderr or proc.stdout)[-800:]}
        return {"status": "probed", "probe": payload, "probe_file": str(output_path)}
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "error": "chart_parse.py probe 超过分配时限。"}
    except Exception as exc:
        return {"status": "error", "error": f"{type(exc).__name__}: {str(exc)[:500]}"}


def chart_spec(path: Path, probe: dict[str, Any]) -> dict[str, Any]:
    ticks = probe.get("tick_candidates") or {}
    def positions(axis: str) -> list[Any]:
        candidates = ticks.get(axis) or []
        major = [item.get("px") for item in candidates if item.get("major")]
        selected = major if len(major) >= 2 else [item.get("px") for item in candidates]
        return [item for item in selected if item is not None][:20]
    return {
        "image": str(path),
        "frame_px": probe.get("geometry", {}).get("frame_px"),
        "x": {"ticks_px": positions("x"), "values": [], "label": None, "unit": None},
        "y": {"ticks_px": positions("y"), "values": [], "label": None, "unit": None},
        "series": [],
        "detect_legend": True,
        "instructions": "values 必须与 ticks_px 等长且逐字读自原图；series.name/color 经图例核对后填写；无法读取保持原样。",
    }


def material_caption_candidates(material: Any, source: Path) -> list[dict[str, Any]]:
    """Index text captions/paragraphs for the Agent; never infer missing pixels."""
    candidates: list[dict[str, Any]] = []
    pattern = re.compile(r"(?:\bfig(?:ure)?\.?\s*\d+|\btable\s+\d+|图\s*\d+|表\s*\d+)", re.I)
    stack: list[tuple[Any, str]] = [(material, "")]
    while stack and len(candidates) < 300:
        value, pointer = stack.pop()
        if isinstance(value, dict):
            identifier = next((str(value[key]) for key in ID_KEYS if value.get(key)), None)
            text = next((value[key] for key in ("caption", "text", "content", "title")
                         if isinstance(value.get(key), str)), None)
            if text and (identifier or pattern.search(text)):
                candidates.append({"paragraph_id": identifier or "not_reported", "text": text[:8000],
                                   "source_id": str(source), "location": source.name + "#" + pointer})
            for key, item in value.items():
                if isinstance(item, (dict, list)):
                    stack.append((item, pointer + "/" + pointer_token(key)))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, (dict, list)):
                    stack.append((item, pointer + "/" + str(index)))
    return candidates or [nr("material.json 中未识别到图注/表注候选；需人工阅读源对象。")]


def sanitize_check(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": not report.get("problems"),
        "problems": nonempty_list(report.get("problems"), "契约检查未报告问题。"),
        "notes": nonempty_list(report.get("notes"), "契约检查未报告附加说明。"),
    }


def run_contract_check(skill_dir: Path, result_path: Path, contract_path: Path) -> dict[str, Any]:
    script = skill_dir / "scripts" / "contract.py"
    if not script.is_file():
        return {"ok": False, "problems": [nr("contract.py 不存在，无法执行机检。")],
                "notes": [nr("请人工核对输出契约。")]}
    try:
        proc = subprocess.run([sys.executable, str(script), "check", str(result_path),
                               "--family", "reading", "--contract", str(contract_path)],
                              capture_output=True, text=True, timeout=10)
        report = json.loads(proc.stdout)
        report["ok"] = proc.returncode == 0 and not report.get("problems")
        return sanitize_check(report)
    except Exception as exc:
        return {"ok": False, "problems": [nr(f"契约机检执行失败：{type(exc).__name__}: {exc}")],
                "notes": [nr("请人工核对输出契约。")]}


def normalize_review(review: dict[str, Any], section: str, object_id: str) -> dict[str, Any]:
    owner = review.get(section) or {}
    if isinstance(owner, list):
        for item in owner:
            if isinstance(item, dict) and str(item.get("id") or item.get("paragraph_id")) == object_id:
                return item
        return {}
    value = owner.get(object_id) if isinstance(owner, dict) else {}
    return value if isinstance(value, dict) else {}


def export_points(output_dir: Path, images: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for image in images:
        image_id = image.get("image_id", "not_reported")
        points = image.get("data_points")
        if not isinstance(points, list):
            continue
        for index, point in enumerate(points):
            if not isinstance(point, dict) or point.get("status") == "not_reported":
                continue
            rows.append({"image_id": image_id, "point_index": index,
                         "series": point.get("series", "not_reported"),
                         "x": point.get("x", "not_reported"), "y": point.get("y", "not_reported"),
                         "x_unit": point.get("x_unit", "not_reported"),
                         "y_unit": point.get("y_unit", "not_reported"),
                         "location": point.get("location", point.get("locator", "not_reported"))})
    if not rows:
        return nr("没有经视觉复核并带数据坐标的点，未生成点数据 CSV。")
    path = output_dir / "digitized_points.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return {"file": str(path), "rows": len(rows),
            "uncertainty": "近似像素读数；精度、误差棒和读取条件见 result.json，不能当作论文原始数据。"}


def execute(args: argparse.Namespace) -> int:
    started = time.monotonic()
    deadline = started + args.timeout
    case_path = args.case.expanduser().resolve()
    output_path = args.output.expanduser().resolve()
    output_dir = output_path.parent
    work_dir = (args.work_dir or Path("/app/work")).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    if output_path.is_file():
        # Preserve even a malformed previous draft without letting it block the new run.
        (work_dir / "result.previous.json").write_bytes(output_path.read_bytes())

    if not case_path.is_file():
        final = {
            "case_id": "not_reported", "status": "input_error",
            "result": nr(f"输入文件不存在：{case_path}"),
            "claims": [claim("input_error", "case.json 不存在，无法开始解析。", case_path,
                             str(case_path), "case file not found")],
            "uncertainties": [{"kind": "missing_input", "description": str(case_path),
                               "handoff": "检查 /input/case.json 挂载。"}],
            "limitations": ["需要运行环境提供 /input/case.json 后重跑。"],
            "sources": [nr("case.json 不存在，没有材料来源可登记。")],
            "change_log": [{"stage": "input", "action": "检查输入文件", "outcome": "not_found"}],
        }
        write_json(output_path, final)
        print(json.dumps({"output": str(output_path), "status": "input_error", "exit": 2}, ensure_ascii=False))
        return 2
    try:
        case = read_json(case_path)
        if not isinstance(case, dict):
            raise ValueError("case.json 顶层必须是对象")
    except Exception as exc:
        final = {
            "case_id": "not_reported", "status": "input_error",
            "result": nr(f"case.json 无法解析：{type(exc).__name__}: {exc}"),
            "claims": [claim("input_error", "case.json 解析失败。", case_path,
                             str(case_path), str(exc))],
            "uncertainties": [{"kind": "invalid_input", "description": str(exc),
                               "handoff": "修复 JSON 后重跑。"}],
            "limitations": ["输入不是有效 JSON 对象，未读取任何科学材料。"],
            "sources": [{"file": str(case_path), "readable": False}],
            "change_log": [{"stage": "input", "action": "解析 JSON", "outcome": "input_error"}],
        }
        write_json(output_path, final)
        print(json.dumps({"output": str(output_path), "status": "input_error", "exit": 2}, ensure_ascii=False))
        return 2

    skill_dir = Path(args.skill_dir).resolve() if args.skill_dir else Path(__file__).resolve().parent.parent
    case_id = case.get("case_id") if case.get("case_id") not in (None, "") else "not_reported"
    contract = case.get("output_contract") if isinstance(case.get("output_contract"), dict) else DEFAULT_CONTRACT
    contract_path = work_dir / "output_contract.json"
    write_json(contract_path, contract)
    images_in = image_declarations(case)
    table_ids = table_declarations(case)
    scope = case.get("organizer_supplied_scope") or {}
    material_paths = material_candidates(case, case_path.parent, work_dir)
    material_path = material_paths[0] if material_paths else None
    material: Any = None
    material_error = None
    if material_path:
        try:
            material = read_json(material_path)
        except Exception as exc:
            material_error = f"{type(exc).__name__}: {exc}"

    review: dict[str, Any] = {}
    review_error = None
    if args.review:
        try:
            candidate = read_json(args.review.expanduser().resolve())
            if not isinstance(candidate, dict):
                raise ValueError("review 顶层必须是对象")
            review = candidate
        except Exception as exc:
            review_error = f"{type(exc).__name__}: {exc}"

    changes: list[dict[str, Any]] = [
        {"stage": "contract", "action": "读取 case_id、output_contract 与 supplied scope",
         "output": str(contract_path)},
        {"stage": "inventory", "action": "按声明路径及输入目录发现材料",
         "found_material": str(material_path) if material_path else "not_reported"},
    ]
    uncertainties: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    claims: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = [{"source_id": "case.json", "path": str(case_path), "readable": True}]
    if case_id == "not_reported":
        uncertainties.append({"kind": "missing_case_id", "description": "case.json 未提供 case_id，未猜测标识。",
                              "handoff": "由运行方补齐输入标识。"})
    if review_error:
        warnings.append({"code": "review_not_loaded", "message": review_error,
                         "action": "保留自动抽取结果并要求人工修复 visual_review.json。"})
    if scope.get("unprovided_objects"):
        uncertainties.append({"kind": "unprovided_scope", "description": str(scope["unprovided_objects"]),
                              "effect": "未给像素的其他图只登记缺口，不构造轴、点、误差线或数据，也不影响必做对象完成判定。"})

    # Scope claims use literal quotes from the supplied case, not paper metadata.
    if images_in:
        locator, quote = source_line(case_path, images_in[0]["file"])
        claims.append(claim("required_image_scope", f"必做图片清单含 {len(images_in)} 张声明图片。",
                            case_path, locator, quote))
    if table_ids:
        locator, quote = source_line(case_path, table_ids[0])
        claims.append(claim("required_table_scope", f"必做字面原表共 {len(table_ids)} 张；清单首项为 {table_ids[0]}。",
                            case_path, locator, quote, table_ids[0]))

    tables: list[dict[str, Any]] = []
    table_export: list[dict[str, Any]] = []
    tables_found = 0
    tables_complete = 0
    for table_id in table_ids:
        found = find_object_by_id(material, table_id) if material is not None else None
        table_review = normalize_review(review, "tables", table_id)
        if material_path is None:
            reason = "material.json 未在声明位置或输入目录找到，无法取得该字面原表。"
        elif material_error:
            reason = f"material.json 无法解析：{material_error}"
        elif found is None:
            reason = f"material.json 已读取，但未找到 paragraph/table id={table_id}。"
        else:
            reason = ""
        source_pointer = found[1] if found else "not_reported"
        locator = (f"{material_path.name}#{source_pointer}" if found else
                   nr(f"未在 {material_path or 'material.json'} 中找到 {table_id}；不构造源定位。"))
        table: dict[str, Any] = {
            "table_id": table_id,
            "paragraph_id": table_id,
            "material_status": "found" if found else "not_found",
            "source_id": str(material_path) if material_path else "not_reported",
            "location": locator,
            "review_complete": table_review.get("review_complete") is True,
        }
        if found:
            tables_found += 1
            payload, pointer = found
            normalized = normalize_table(payload)
            table["literal_source"] = payload
            table["headers"] = table_review.get("headers", normalized["headers"])
            table["rows"] = table_review.get("rows", normalized["rows"])
            table["caption"] = table_review.get("caption", nr("见 literal_source；未自动选择可能错配的表题。"))
            table["grouping_and_conditions"] = table_review.get(
                "grouping_and_conditions", nr("需结合 literal_source、表题和上下文逐字核对。"))
            table["units"] = table_review.get("units", nr("未自动推断单位；请从逐字表头/表注记录。"))
            table["footnotes"] = table_review.get("footnotes", nr("未取得经复核的表注。"))
            table["uncertainty_or_statistics"] = table_review.get(
                "uncertainty_or_statistics", nr("未取得经复核的误差、n 或显著性语义。"))
            table["trends"] = table_review.get("trends", nr("未自动由原表推断趋势。"))
            claims.append(claim("literal_table", f"已逐字保留原表 {table_id} 的源对象。",
                                material_path, f"{material_path.name}#{pointer}", table_id, table_id,
                                {"verbatim_preservation": True, "numeric_type_coercion": False}))
        else:
            for field in TABLE_FIELDS:
                table[field] = nr(reason)
            uncertainties.append({"kind": "missing_table", "table_id": table_id,
                                  "description": reason, "handoff": "提供包含该 ID 的 material.json 后重跑。"})
            claims.append(claim("missing_table", f"原表 {table_id} 未取得。", case_path,
                                source_line(case_path, table_id)[0], source_line(case_path, table_id)[1], table_id))
        for key, value in table_review.items():
            if key in TABLE_FIELDS:
                table[key] = value
        for field in TABLE_FIELDS:
            table[field] = nonempty_list(table.get(field), f"{table_id} 的 {field} 未报告；需人工核对源表。") \
                if isinstance(table.get(field), list) else table.get(field, nr("该字段尚未复核。"))
        if table["review_complete"]:
            tables_complete += 1
        tables.append(table)
        table_export.append({"table_id": table_id, "source_id": table["source_id"],
                             "location": table["location"], "headers": table["headers"],
                             "rows": table["rows"], "literal_source": table.get("literal_source", nr(reason))})

    if material_path:
        sources.append({"source_id": "material.json", "path": str(material_path),
                        "readable": material_error is None})
    elif table_ids:
        sources.append({"source_id": "material.json", "path": "material.json", "readable": False,
                        "reason": "声明需要的 material.json 未找到。"})

    images: list[dict[str, Any]] = []
    images_found = 0
    images_complete = 0
    successful_probes = 0
    review_template: dict[str, Any] = {"case_id": case_id, "images": {}, "tables": {},
                                       "answer": nr("在此写逐问主答案；不要用本模板值代替阅读。"),
                                       "claims": [], "uncertainties": [],
                                       "unprovided_objects": []}
    for item in images_in:
        image_id, declared = item["id"], item["file"]
        actual = find_declared_file(declared, case_path.parent, work_dir)
        image_review = normalize_review(review, "images", image_id)
        absent_reason = f"声明图片 {declared} 在 case/input/work 候选路径均未找到。"
        image: dict[str, Any] = {
            "image_id": image_id, "declared_path": declared,
            "material_status": "found" if actual else "not_found",
            "source_id": str(actual) if actual else "not_reported",
            "actual_path": str(actual) if actual else "not_reported",
            "declared_sha256": item["sha256"] or "not_reported",
            "review_complete": image_review.get("review_complete") is True,
        }
        probe_result: dict[str, Any] = {"status": "not_attempted"}
        if actual:
            images_found += 1
            # Even if the deterministic probe fails, the Agent can still read the real raster.
            review_template["images"][image_id] = {
                "review_complete": False,
                "visual_evidence": {"source_id": str(actual), "location": f"img:{actual.name}",
                                    "paragraph_id": "从 material.json 图注候选中填写"},
                **{field: nr("查看原图后填写；不存在的定量对象用 not_applicable 并说明。")
                   for field in IMAGE_FIELDS},
                "claims": [],
            }
            observed_sha = sha256(actual)
            image["observed_sha256"] = observed_sha
            image["sha256_match"] = observed_sha == item["sha256"] if item["sha256"] else "not_reported"
            if item["sha256"] and observed_sha != item["sha256"]:
                warnings.append({"code": "sha256_mismatch", "image_id": image_id,
                                 "declared": item["sha256"], "observed": observed_sha})
            sources.append({"source_id": image_id, "path": str(actual), "sha256": observed_sha})
            probe_result = probe_image(actual, skill_dir, work_dir, image_id, deadline)
            image["probe_status"] = probe_result["status"]
            if probe_result["status"] == "probed":
                successful_probes += 1
                probe = probe_result["probe"]
                image["probe_classification_candidate"] = probe.get("verdict", "not_reported")
                image["probe_geometry"] = probe.get("geometry", nr("探测结果没有几何字段。"))
                image["probe_locator"] = probe_result["probe_file"]
                spec_path = work_dir / (re.sub(r"[^A-Za-z0-9_.-]", "_", image_id) + ".chart_spec.json")
                write_json(spec_path, chart_spec(actual, probe))
                review_template["images"][image_id] = {
                    "review_complete": False,
                    "chart_parse_spec": str(spec_path),
                    "visual_evidence": {"source_id": str(actual), "location": f"img:{actual.name}",
                                        "paragraph_id": "从 material.json 图注候选中填写"},
                    **{field: nr("查看原图后填写；照片/示意图中不存在的定量对象用 not_applicable 并说明。")
                       for field in IMAGE_FIELDS},
                    "claims": [],
                }
            else:
                image["probe_error"] = probe_result.get("error", "not_reported")
                warnings.append({"code": "image_probe_" + probe_result["status"],
                                 "image_id": image_id, "message": probe_result.get("error", "not_reported")})
            claims.append(claim("image_inventory", f"已找到并探测声明图片 {image_id}；pixel 结构必须再由视觉复核。",
                                actual, f"img:{actual.name}", f"actual file: {actual.name}; sha256: {observed_sha}"))
        else:
            image["probe_status"] = "not_attempted_missing_file"
            uncertainties.append({"kind": "missing_image", "image_id": image_id,
                                  "description": absent_reason,
                                  "handoff": "按案例声明提供该 PNG/JPEG，核对 sha256 后重跑。"})
            claims.append(claim("missing_image", f"声明图片 {image_id} 未取得，不能描述其图型或内容。",
                                case_path, source_line(case_path, declared)[0],
                                source_line(case_path, declared)[1]))
        default_reason = absent_reason if not actual else "图片已定位，但该字段尚未完成实际视觉复核。"
        for field in IMAGE_FIELDS:
            image[field] = image_review.get(field, nr(default_reason))
        image["visual_evidence"] = image_review.get(
            "visual_evidence", nr("完成视觉复核时记录图注 paragraph_id、原文短引文和像素定位。"))
        image["review_complete"] = image_review.get("review_complete") is True
        if image["review_complete"]:
            images_complete += 1
        for extra in image_review.get("claims", []) if isinstance(image_review.get("claims"), list) else []:
            if isinstance(extra, dict):
                claims.append(extra)
        images.append(image)

    for table in tables:
        table_review = normalize_review(review, "tables", table["table_id"])
        if not review_template["tables"]:
            review_template["tables"] = {}
        review_template["tables"][table["table_id"]] = {
            "review_complete": False,
            **{field: table.get(field, nr("查看 literal_source 后填写。")) for field in TABLE_FIELDS},
        }
    write_json(work_dir / "visual_review.template.json", review_template)
    if material is not None and material_path:
        captions = material_caption_candidates(material, material_path)
        write_json(work_dir / "caption_candidates.json", captions)
    else:
        captions = [nr("material.json 未取得，无法列出未给像素图注的具体清单。")]

    extra_claims = review.get("claims") if isinstance(review.get("claims"), list) else []
    claims.extend(item for item in extra_claims if isinstance(item, dict))
    extra_uncertainties = review.get("uncertainties") if isinstance(review.get("uncertainties"), list) else []
    uncertainties.extend(item if isinstance(item, dict) else {"kind": "review_note", "description": str(item)}
                         for item in extra_uncertainties)
    review_warnings = review.get("warnings") if isinstance(review.get("warnings"), list) else []
    warnings.extend(item if isinstance(item, dict) else {"code": "review_warning", "message": str(item)}
                    for item in review_warnings)
    if "unprovided_objects" in review:
        unprovided = review.get("unprovided_objects")
    elif material is None:
        unprovided = captions
    else:
        unprovided = nr("图注候选已写入 /app/work/caption_candidates.json；尚未逐项判定哪些未提供像素。")
    total = len(images_in) + len(table_ids)
    found = images_found + tables_found
    complete = images_complete + tables_complete
    if case_id == "not_reported":
        status = "input_error"
    elif total == 0:
        status = "insufficient_evidence"
        uncertainties.append({"kind": "no_required_scope", "description": "案例未声明必做图片或原表。",
                              "handoff": "人工阅读 task_instruction 并明确解析对象。"})
    elif found < total:
        status = "input_error" if found == 0 else "partial"
    elif complete == total and has_task_answer(review.get("answer")):
        # Human visual review remains valid even when an optional pixel probe is unavailable.
        status = "answered"
    elif complete == total:
        status = "partial"
        uncertainties.append({"kind": "missing_task_answer",
                              "description": "全部对象已复核，但 task_answer 仍是 not_reported。",
                              "handoff": "按 task_instruction 逐问填写 answer，并区分直接读取、合理推断和无法判断。"})
    elif successful_probes == 0 and tables_found == 0:
        status = "insufficient_evidence"
    elif review.get("status") == "insufficient_evidence" and complete == 0:
        status = "insufficient_evidence"
    else:
        status = "partial"
    if time.monotonic() >= deadline:
        status = "partial" if found else "input_error"
        warnings.append({"code": "deadline_reached", "message": "已达到脚本内部时限；保留已落盘结果。"})

    limitations: list[Any]
    if status == "answered":
        limitations = [nr("必做对象均已复核；未提供像素的对象及各项 not_reported 仍按 uncertainties 处理。")]
    else:
        limitations = nonempty_list([
            {"status": status, "required_objects": total, "found_objects": found,
             "reviewed_objects": complete,
             "reason": "声明对象缺失、不可读或尚未全部视觉复核；详细对象级原因见 uncertainties。"},
            {"handoff": "先修复缺失输入；随后在 /app/work/visual_review.json 补证据化视觉复核，并用 --review 重跑。"},
        ], "未记录完成限制。")
    if status == "input_error" and found == 0:
        limitations.append("本次没有取得任何必做原图或原表，result 中不包含科学读数；不得沿用其他案例或论文记忆补写。")

    points_csv = export_points(output_dir, images)
    tables_output = output_dir / "tables.json"
    write_json(tables_output, {"case_id": case_id,
                               "tables": nonempty_list(table_export, "没有可导出的字面原表；原因见 result.json。")})
    elapsed = round(time.monotonic() - started, 3)
    changes.extend([
        {"stage": "extract", "action": "逐字提取 required_literal_source_table_ids",
         "tables_found": tables_found, "tables_required": len(table_ids)},
        {"stage": "probe", "action": "探测 required_actual_image_files，不自动赋刻度值",
         "images_found": images_found, "successful_probes": successful_probes},
        {"stage": "deliver", "action": "写出结构化结果与原表导出",
         "outputs": [str(output_path), str(tables_output)]},
    ])
    result_body = {
        "summary": f"必做对象 {total} 项；找到 {found} 项，完成视觉/语义复核 {complete} 项。状态 {status}。",
        "task_answer": review.get("answer", nr("尚未提供逐问视觉复核答案；请先读原图、全部原表和任务说明。")),
        "scope": {
            "id": scope.get("id", "not_reported"),
            "required_images": nonempty_list(images_in, "案例未声明必做图片。"),
            "required_table_ids": nonempty_list(table_ids, "案例未声明必做原表。"),
            "unprovided_objects": nonempty_list(unprovided, "未提供对象清单未报告；未据此构造科学内容。"),
        },
        "images": nonempty_list(images, "案例未声明必做图片；请人工确认任务边界。"),
        "tables": nonempty_list(tables, "案例未声明必做原表；请人工确认任务边界。"),
        "artifacts": {"literal_tables_json": str(tables_output), "digitized_points_csv": points_csv},
    }
    if review.get("editable_artifacts"):
        result_body["editable_artifacts"] = review["editable_artifacts"]
    final: dict[str, Any] = {
        "case_id": case_id,
        "status": status,
        "result": result_body,
        "claims": nonempty_list(claims, "没有可用于科学结论的材料；输入状态见 result.summary。"),
        "uncertainties": nonempty_list(uncertainties, "没有新增不确定性；各对象 not_reported 项仍须逐项查看。"),
        "limitations": limitations,
        "sources": nonempty_list(sources, "没有可登记的输入来源。"),
        "change_log": changes,
        "warnings": nonempty_list(warnings, "运行未产生额外技术警告。"),
        "runtime": {"skill_version": VERSION, "elapsed_seconds_script_only": elapsed,
                    "deadline_seconds_script_only": args.timeout,
                    "model_requests_by_scripts": 0, "network_access_by_scripts": "not_used",
                    "note": "此处仅计本脚本墙钟时间；Agent 视觉阅读时间由平台记录，不能据此声称整个案例总耗时。"},
        "contract_check": {"ok": False, "problems": [nr("初次检查尚未执行。")],
                           "notes": [nr("将在写出初稿后更新。")]},
    }
    # Any case-specific top-level contract key is preserved explicitly.
    for key in contract:
        if key not in final:
            final[key] = nr(f"案例 output_contract 声明了字段 {key}，当前证据尚未提供其专用映射；需人工复核。")
            final["uncertainties"].append({"kind": "unmapped_contract_field", "field": key,
                                           "handoff": "按 /input/case.json 的原始字段定义补齐。"})
    write_json(output_path, final)  # early, complete draft before final checks
    check = run_contract_check(skill_dir, output_path, contract_path)
    final["contract_check"] = check
    if not check.get("ok"):
        final["warnings"] = nonempty_list(warnings, "运行未产生额外技术警告。")
        final["warnings"].append({"code": "contract_check_failed", "message": check["problems"]})
        if final["status"] == "answered":
            final["status"] = "partial"
    final["result"]["summary"] = (f"必做对象 {total} 项；找到 {found} 项，完成视觉/语义复核 "
                                  f"{complete} 项。状态 {final['status']}。")
    final["runtime"]["elapsed_seconds_script_only"] = round(time.monotonic() - started, 3)
    changes.append({"stage": "check", "action": "执行 contract.py，再回写结构化检查报告",
                    "ok": check.get("ok"), "output": str(output_path)})
    write_json(output_path, final)
    write_json(work_dir / "run_summary.json", {
        "case_id": case_id, "status": final["status"], "output": str(output_path),
        "required": total, "found": found, "reviewed": complete,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "within_script_deadline": time.monotonic() <= deadline,
        "contract_ok": check.get("ok"), "claims": len(final["claims"]),
        "uncertainties": len(final["uncertainties"]),
    })
    print((work_dir / "run_summary.json").read_text(encoding="utf-8"), end="")
    return 0 if check.get("ok") else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Run supplied-scope scientific figure reading")
    parser.add_argument("case", type=Path, help="normally /input/case.json")
    parser.add_argument("-o", "--output", type=Path, default=Path("/app/output/result.json"))
    parser.add_argument("--work-dir", type=Path, default=Path("/app/work"))
    parser.add_argument("--review", type=Path, help="evidence-linked visual_review.json from the Agent")
    parser.add_argument("--skill-dir", type=Path, default=os.environ.get("SKILL_DIR"))
    parser.add_argument("--timeout", type=float, default=105,
                        help="internal script budget; leave time for Agent review and final audit")
    return execute(parser.parse_args())


if __name__ == "__main__":
    sys.exit(main())
