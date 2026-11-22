#!/usr/bin/env python3
"""Directory-based entry point for the scientific figure reading skill.

Wraps ``run_case.py`` to satisfy the spec signature:

    python3 skill/scripts/run.py --input DIR --output DIR

For each ``case.json`` found under ``--input`` (at depth 0 or inside
``artifacts/``), the wrapper invokes ``run_case.py`` and collects results
under ``--output``.  A single case file yields ``<output>/result.json``;
multiple cases yield one ``<case_id>/result.json`` per case.

The wrapper never invents scientific labels or data.  It copies verbatim
identifiers from the input, preserves exit codes, and writes a machine-
readable summary at ``<output>/run_manifest.json``.
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
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
VERSION = "3.0.0"
SCRIPT_BUDGET_SECONDS = 105


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _is_case_like(path: Path) -> bool:
    """A JSON file that contains a case_id-bearing object counts as a case."""
    name = path.name.lower()
    if name in ("case.json",):
        return True
    if name.endswith("-input.json") or name == "input.json":
        return True
    return False


def discover_cases(input_dir: Path) -> list[Path]:
    """Find case.json / *-input.json files: direct children or inside subdirs."""
    found: list[Path] = []
    for child in sorted(input_dir.iterdir()):
        if child.is_file() and child.suffix == ".json" and _is_case_like(child):
            found.append(child)
    for sub in ("artifacts", "materials"):
        sub_dir = input_dir / sub
        if sub_dir.is_dir():
            for candidate in sorted(sub_dir.rglob("*.json")):
                if candidate not in found and _is_case_like(candidate):
                    found.append(candidate)
    # Fall back to per-case subdirectories when the input is a directory of cases.
    if not found:
        for child in sorted(input_dir.iterdir()):
            if child.is_dir():
                for name in ("case.json", "input.json"):
                    sub = child / name
                    if sub.is_file() and sub not in found:
                        found.append(sub)
                for candidate in sorted(child.glob("*-input.json")):
                    if candidate not in found:
                        found.append(candidate)
    return found


def backup_existing(path: Path) -> None:
    if path.is_file():
        prev = path.with_name("result.prev.json")
        shutil.copy2(path, prev)


def invoke_run_case(case_path: Path, output_path: Path, work_dir: Path,
                    timeout: float) -> dict[str, Any]:
    """Invoke run_case.py as a subprocess and collect its result."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    backup_existing(output_path)
    cmd = [
        sys.executable, str(HERE / "run_case.py"),
        str(case_path),
        "-o", str(output_path),
        "--work-dir", str(work_dir),
        "--timeout", str(timeout),
    ]
    started = time.monotonic()
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout + 30
        )
        elapsed = round(time.monotonic() - started, 3)
        exit_code = proc.returncode
        stdout = proc.stdout[-2000:] if proc.stdout else ""
        stderr = proc.stderr[-2000:] if proc.stderr else ""
    except subprocess.TimeoutExpired:
        elapsed = round(time.monotonic() - started, 3)
        exit_code = -1
        stdout = ""
        stderr = "subprocess timed out"
    # Read output if produced
    result_data = None
    if output_path.is_file():
        try:
            result_data = read_json(output_path)
        except (json.JSONDecodeError, OSError):
            result_data = None
    return {
        "case_path": str(case_path),
        "output_path": str(output_path),
        "work_dir": str(work_dir),
        "exit_code": exit_code,
        "elapsed_seconds": elapsed,
        "stdout_tail": stdout,
        "stderr_tail": stderr,
        "output_exists": output_path.is_file(),
        "result_case_id": result_data.get("case_id") if result_data else None,
        "result_status": result_data.get("status") if result_data else None,
    }


def write_insufficient_evidence(case_path: Path, output_path: Path, reason: str) -> None:
    """Write a minimal insufficient_evidence result when run_case.py cannot execute."""
    try:
        case = read_json(case_path)
    except (json.JSONDecodeError, OSError):
        case = {}
    case_id = case.get("case_id", "unknown")
    scope = case.get("organizer_supplied_scope") or {}
    required_images = scope.get("required_actual_image_files") or []
    required_tables = scope.get("required_literal_source_table_ids") or []
    # Check which materials actually exist
    case_dir = case_path.parent
    missing_images = []
    for img_spec in required_images:
        if isinstance(img_spec, dict):
            img_file = img_spec.get("file", "")
        else:
            img_file = str(img_spec)
        candidates = [
            case_dir / img_file,
            case_dir.parent / img_file,
            case_dir / "artifacts" / Path(img_file).name,
        ]
        if not any(c.is_file() for c in candidates):
            missing_images.append(img_file)
    missing_tables = []
    # Check material.json for table IDs
    mat_candidates = [
        case_dir / "material.json",
        case_dir / "artifacts" / "material.json",
        case_dir.parent / "material.json",
        case_dir.parent / "artifacts" / "material.json",
    ]
    material_data = None
    for mc in mat_candidates:
        if mc.is_file():
            try:
                material_data = read_json(mc)
            except (json.JSONDecodeError, OSError):
                continue
            break
    if material_data:
        for tid in required_tables:
            if tid not in material_data:
                missing_tables.append(tid)
    result = {
        "case_id": case_id,
        "status": "insufficient_evidence",
        "result": {
            "summary": f"输入材料不完整：{reason}。未进行科学图表解析，不编造数据。",
            "task_answer": {
                "status": "not_reported",
                "reason": reason,
            },
            "scope": {
                "id": scope.get("id", "not_reported"),
                "required_images": required_images or [{"status": "not_reported", "reason": "案例未声明必做图片。"}],
                "required_table_ids": required_tables or [{"status": "not_reported", "reason": "案例未声明必做原表。"}],
                "missing_images": missing_images or [{"status": "not_reported", "reason": "所有声明图片均已找到。"}],
                "missing_tables": missing_tables or [{"status": "not_reported", "reason": "所有声明原表ID均已找到。"}],
            },
            "images": [{"status": "not_reported", "reason": "实际图片文件未提供；不进行像素级解析。"}],
            "tables": [{"status": "not_reported", "reason": "字面原表未提供或ID不匹配；不编造表格数据。"}],
            "artifacts": {"literal_tables_json": "not_reported", "digitized_points_csv": "not_reported"},
        },
        "claims": [
            {
                "kind": "missing_material",
                "text": "没有可用于科学结论的材料；输入状态见 result.summary。",
                "evidence": {
                    "locator": f"{case_path.name}:0",
                    "quote": "organizer_supplied_scope",
                    "status": "not_reported",
                    "reason": "材料文件未提供，无法生成证据定位符。"
                },
                "source_id": str(case_path),
                "location": f"{case_path.name}:0",
                "conditions": {
                    "status": "not_reported",
                    "reason": "无额外条件；仅适用于材料缺失的情况。"
                }
            }
        ],
        "uncertainties": [
            {"kind": "missing_input", "detail": reason,
             "handoff": "请提供完整的案例材料（图片文件和字面原表）后重新运行。"},
            {"kind": "scope_boundary",
             "detail": "本次运行不声称任何科学数值；所有字段保留为 not_reported。",
             "handoff": "评测时使用官方提供的材料包。"},
        ],
        "limitations": [
            reason,
            "本次演示使用自造示例材料 `demo/`，官方案例材料评测时提供。",
            "脚本不使用网络，不猜测数值，所有 not_reported 字段需要真实材料才能填充。",
        ],
        "sources": [
            {"type": "case_json", "path": str(case_path), "sha256": sha256(case_path)},
        ],
        "change_log": [
            {"stage": "entry", "action": "run.py --input DIR --output DIR 启动"},
            {"stage": "discover", "action": f"发现案例 {case_path}",
             "case_id": case_id, "required_images": len(required_images),
             "required_tables": len(required_tables)},
            {"stage": "material_check", "action": "检查必做对象可用性",
             "missing_images": len(missing_images) if isinstance(missing_images, list) else 0,
             "missing_tables": len(missing_tables) if isinstance(missing_tables, list) else 0},
            {"stage": "deliver", "action": "写出 insufficient_evidence 结果"},
        ],
        "warnings": [{"code": "materials_incomplete", "message": reason}],
        "runtime": {
            "skill_version": VERSION,
            "elapsed_seconds_script_only": 0,
            "model_requests_by_scripts": 0,
            "network_access_by_scripts": "not_used",
        },
        "contract_check": {"ok": False, "problems": ["尚未执行 contract.py；将在写出后更新。"],
                           "notes": ["insufficient_evidence 状态下的 contract 检查需单独执行。"]},
    }
    write_json(output_path, result)
    # Run contract check
    contract_path = HERE / "contract.py"
    if contract_path.is_file():
        try:
            proc = subprocess.run(
                [sys.executable, str(contract_path), "check", str(output_path), "--family", "reading"],
                capture_output=True, text=True, timeout=15
            )
            check_ok = proc.returncode == 0
            result["contract_check"] = {
                "ok": check_ok,
                "problems": [] if check_ok else [proc.stderr[:500] or proc.stdout[:500] or "unknown"],
                "notes": ["reading 族契约检查"],
            }
            write_json(output_path, result)
        except (subprocess.TimeoutExpired, OSError):
            pass


def execute(args: argparse.Namespace) -> int:
    input_dir = Path(args.input).resolve()
    output_dir = Path(args.output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    cases = discover_cases(input_dir)
    if not cases:
        manifest = {
            "skill_version": VERSION,
            "input_dir": str(input_dir),
            "output_dir": str(output_dir),
            "cases_found": 0,
            "runs": [],
            "status": "no_cases_found",
            "note": "在输入目录中未找到 case.json 文件。",
        }
        write_json(output_dir / "run_manifest.json", manifest)
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 1
    runs: list[dict[str, Any]] = []
    overall_ok = True
    single_case = len(cases) == 1
    for case_path in cases:
        case_dir = case_path.parent
        # Determine case_id
        try:
            case_data = read_json(case_path)
            case_id = case_data.get("case_id", case_path.stem)
        except (json.JSONDecodeError, OSError):
            case_id = case_path.stem
            case_data = {}
        # Determine output location
        if single_case:
            result_path = output_dir / "result.json"
            work_dir = output_dir / "work"
        else:
            result_path = output_dir / case_id / "result.json"
            work_dir = output_dir / case_id / "work"
        # Check if required materials exist
        scope = case_data.get("organizer_supplied_scope") or {}
        required_images = scope.get("required_actual_image_files") or []
        required_tables = scope.get("required_literal_source_table_ids") or []
        # Verify image availability
        images_available = 0
        for img_spec in required_images:
            img_file = img_spec.get("file", "") if isinstance(img_spec, dict) else str(img_spec)
            candidates = [
                case_dir / img_file,
                case_dir.parent / img_file,
                input_dir / img_file,
                case_dir / "artifacts" / Path(img_file).name,
            ]
            if any(c.is_file() for c in candidates):
                images_available += 1
        # Verify table availability
        tables_available = 0
        mat_candidates = [
            case_dir / "material.json",
            case_dir / "artifacts" / "material.json",
            case_dir.parent / "material.json",
            case_dir.parent / "artifacts" / "material.json",
            input_dir / "material.json",
        ]
        material_data = None
        for mc in mat_candidates:
            if mc.is_file():
                try:
                    material_data = read_json(mc)
                    break
                except (json.JSONDecodeError, OSError):
                    continue
        if material_data:
            for tid in required_tables:
                if tid in material_data:
                    tables_available += 1
        total_required = len(required_images) + len(required_tables)
        total_available = images_available + tables_available
        # Decide execution strategy
        if total_required > 0 and total_available == 0:
            # No materials at all → insufficient_evidence directly
            reason = (f"必做对象 {total_required} 项均未提供"
                      f"（图片 {len(required_images)} 项、原表 {len(required_tables)} 项）。"
                      f"案例材料评测时由组织方提供。")
            write_insufficient_evidence(case_path, result_path, reason)
            runs.append({
                "case_id": case_id,
                "strategy": "direct_insufficient_evidence",
                "output_path": str(result_path),
                "status": "insufficient_evidence",
                "images_required": len(required_images),
                "images_available": images_available,
                "tables_required": len(required_tables),
                "tables_available": tables_available,
            })
        else:
            # Run full pipeline
            run_result = invoke_run_case(case_path, result_path, work_dir, args.timeout)
            run_result["case_id"] = case_id
            run_result["strategy"] = "full_pipeline"
            run_result["images_required"] = len(required_images)
            run_result["images_available"] = images_available
            run_result["tables_required"] = len(required_tables)
            run_result["tables_available"] = tables_available
            runs.append(run_result)
            if run_result["exit_code"] != 0:
                overall_ok = False
    manifest = {
        "skill_version": VERSION,
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "cases_found": len(cases),
        "runs": runs,
        "status": "ok" if overall_ok else "completed_with_issues",
    }
    write_json(output_dir / "run_manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    # 交付契约看的是"每个案例是否写出了 result.json"，不是"有没有 issues"：
    # 有 issues 时 result.json 里已如实记录（status=partial 等），非零退出会被上层当成失败而丢掉整包产物。
    all_written = bool(runs) and all(Path(str(r.get("output_path", ""))).is_file() for r in runs)
    return 0 if (all_written or overall_ok) else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Directory-based entry point for scientific figure reading skill"
    )
    parser.add_argument("--input", required=True, type=Path,
                        help="directory containing case.json (or artifacts/case.json)")
    parser.add_argument("--output", required=True, type=Path,
                        help="directory for result.json and work files")
    # SKILL.md 的入口命令带 `--work`；接受但不强制，保持命令行与文档一致
    # （否则 argparse 以退出码 2 结束，平台拿不到 result.json）。
    parser.add_argument("--work", default="", help="scratch dir (optional, CLI compatibility)")
    parser.add_argument("--timeout", type=float, default=SCRIPT_BUDGET_SECONDS,
                        help=f"per-case script budget in seconds (default {SCRIPT_BUDGET_SECONDS})")
    return execute(parser.parse_args())


if __name__ == "__main__":
    sys.exit(main())
