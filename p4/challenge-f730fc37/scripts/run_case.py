#!/usr/bin/env python3
"""Offline single-case entry point for /input/case.json -> /app/output.

The command never submits a prepared answer. It indexes the actual files, binds
statements to locators, builds editable teaching artifacts, renders real previews,
and exits after the contract and evidence checks. Default internal budget is
105 seconds (--seconds can lower it); a fatal document branch still leaves an
honest result.json. Exit 0 means a result was written, not that science was answered.
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
import datetime as _dt
import hashlib
import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))


class Trace:
    def __init__(self, workdir, start):
        self.workdir = workdir
        self.start = start
        self.records = []

    def log(self, message):
        line = f"[run_case +{time.monotonic() - self.start:.2f}s] {message}"
        print(line, file=sys.stderr, flush=True)

    def run(self, name, args, timeout):
        cmd = [sys.executable, str(SCRIPT_DIR / name)] + [str(x) for x in args]
        began = time.monotonic()
        rec = {"script": name, "command": cmd, "timeout_seconds": round(timeout, 2)}
        self.log("RUN " + " ".join(cmd))
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=max(0.5, timeout), cwd=str(self.workdir))
            rec.update(exit_code=proc.returncode, stdout=proc.stdout[-12000:], stderr=proc.stderr[-12000:])
        except subprocess.TimeoutExpired as exc:
            rec.update(exit_code=124, stdout=(exc.stdout or "") if isinstance(exc.stdout, str) else "",
                       stderr=f"timeout after {timeout:.2f}s")
        except OSError as exc:
            rec.update(exit_code=126, stdout="", stderr=f"{type(exc).__name__}: {exc}")
        rec["elapsed_seconds"] = round(time.monotonic() - began, 3)
        self.records.append(rec)
        self.save()
        self.log(f"EXIT {name} rc={rec['exit_code']} elapsed={rec['elapsed_seconds']}s")
        if rec["stdout"].strip():
            self.log("STDOUT " + rec["stdout"].strip()[-1600:])
        if rec["stderr"].strip():
            self.log("STDERR " + rec["stderr"].strip()[-1600:])
        return rec

    def save(self):
        self.workdir.mkdir(parents=True, exist_ok=True)
        (self.workdir / "stage_log.json").write_text(json.dumps(self.records, ensure_ascii=False, indent=1), encoding="utf-8")


def now_iso():
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def nr(reason):
    return {"value": "not_reported", "reason": reason}


def initial_result(case_id):
    return {
        "case_id": case_id,
        "status": "partial",
        "result": {"reading_scope": nr("已创建结果骨架，科研材料索引尚未完成"),
                   "metabolism_core": [nr("等待实际材料提取")],
                   "metabolism_supplements": [nr("等待实际材料提取")],
                   "enzyme_study": nr("等待实际材料提取"),
                   "immunology_review": nr("等待实际材料提取")},
        "claims": [{"kind": "run_state", "evidence": {"locator": "not_reported", "quote": "not_reported",
                    "reason": "尚未读取材料"}, "source_id": "not_reported", "location": "not_reported"}],
        "uncertainties": [{"code": "run_in_progress", "note": "尚无可报告的科研结论；后续阶段只能根据真实文件增量改写"}],
        "limitations": [{"kind": "current_before_reading", "reason": "索引与渲染尚未完成", "handoff": "检查 /app/work/stage_log.json"}],
    }


def load_case(path):
    if not path.is_file():
      return {}, "not_reported", f"输入文件不存在：{path}", None
    data = path.read_bytes()
    try:
        case = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {}, "not_reported", f"case.json 不是有效 UTF-8 JSON：{type(exc).__name__}: {exc}", None
    if not isinstance(case, dict):
        return {}, "not_reported", "case.json 顶层必须是对象", None
    case_id = case.get("case_id")
    error = None if isinstance(case_id, str) and case_id.strip() else "case_id 缺失或不是非空字符串；不得猜测标识"
    return case, str(case_id).strip() if case_id else "not_reported", error, hashlib.sha256(data).hexdigest()


def resolve_materials(case, case_path):
    """Resolve only declared or adjacent input paths, never outputs or package resources."""
    candidates = []
    source = case.get("source") if isinstance(case.get("source"), dict) else {}
    for owner in (case, source):
        for key in ("materials_dir", "material_dir", "materials", "documents", "files"):
            value = owner.get(key)
            values = value if isinstance(value, list) else [value]
            for item in values:
                if isinstance(item, dict):
                    item = item.get("path") or item.get("file") or item.get("source_path")
                if isinstance(item, str) and item.strip():
                    p = Path(item)
                    if not p.is_absolute():
                        p = case_path.parent / p
                    candidates.append(p.resolve())
    candidates += [(case_path.parent / "materials").resolve(), case_path.parent.resolve()]
    for p in candidates:
        if p.is_file() and p.suffix.lower() in {".pdf", ".md", ".markdown", ".txt", ".csv", ".tsv", ".xlsx", ".xlsm", ".docx", ".pptx"}:
            return p.parent, "declared_file_parent"
        if p.is_dir() and any(x.is_file() and not x.name.startswith(".") for x in p.iterdir()):
            return p, "declared_or_adjacent_input_directory"
    return case_path.parent, "input_directory_exists_but_empty_or_unreadable"


def artifact_rel(path, outdir):
    try:
        return str(Path(path).resolve().relative_to(outdir.resolve()))
    except (ValueError, OSError):
        return str(path)


def relative_files(data, outdir):
    """Keep result paths portable; source paths inside input can remain for audit only in work logs."""
    if isinstance(data, dict):
        return {k: relative_files(v, outdir) for k, v in data.items()}
    if isinstance(data, list):
        return [relative_files(v, outdir) for v in data]
    if isinstance(data, Path):
        data = str(data)
    if isinstance(data, str) and data.startswith(str(outdir.resolve()) + os.sep):
        return artifact_rel(data, outdir)
    return data


def add_change(result, stage, detail, start):
    records = result.setdefault("change_log", [])
    records.append({"timestamp_utc": now_iso(), "elapsed_seconds": round(time.monotonic() - start, 3),
                    "stage": stage, "detail": detail, "mutation": "replace_or_append_from_actual_run"})


def checkpoint(result, outdir, workdir, start, stage, detail):
    add_change(result, stage, detail, start)
    previous = outdir / "result.json"
    if previous.is_file():
        try:
            (workdir / "result.previous.json").write_bytes(previous.read_bytes())
        except OSError as exc:
            result.setdefault("uncertainties", []).append({"code": "checkpoint_backup_failed", "note": str(exc)})
    write_json(previous, result)


def required_missing(value):
    if isinstance(value, dict):
        if value.get("value") == "not_reported":
            return True
        return any(required_missing(v) for k, v in value.items() if k not in {"reason", "not_reported_reason", "usage_note", "limitations"})
    if isinstance(value, list):
        return any(required_missing(v) for v in value)
    return False


def fulfill_custom_contract(result, contract):
    aliases = {
        "sources": "source_manifest", "source_files": "source_manifest", "evidence": "teaching_graph",
        "evidence_graph": "teaching_graph", "answer": "result", "limitations": "limitations",
        "artifacts": "artifact_manifest", "deliverables": "artifact_manifest", "warnings": "uncertainties",
        "exceptions": "exception_notes", "human_review": "human_review_manifest",
    }
    filled = []
    for key in (contract or {}):
        if key in result:
            continue
        target = aliases.get(key)
        if target and target in result:
            result[key] = result[target]
        elif key == "result":
            result[key] = {"value": "not_reported", "reason": "主结果已在 reading_scope 所属对象给出；此分支不应发生"}
        else:
            result[key] = nr("原 output_contract 含此附加字段，但实际输入与已执行工具未产生可核验内容；字段保留而未补造")
        filled.append(key)
    return filled


def move_relation_payload(answer, source_info):
    res, claims = answer["result"], answer["claims"]
    by_id = {c.get("claim_id"): c for c in claims if c.get("claim_id")}
    def evidence_for(refs):
        pairs = []
        for ref in refs:
            if not isinstance(ref, str) or ref.startswith(("not_reported", "not_applicable")):
                continue
            c = by_id.get(ref)
            if c is None:
                continue
            pairs.append({"claim_id": ref, "source_id": c.get("source_id"), "locator": c.get("location"),
                          "quote": c["evidence"].get("quote"), "scope": c.get("scope"),
                          "polarity": c.get("polarity", "asserted")})
        return pairs or [{"claim_id": "not_reported", "reason": "未找到可独立回链的证据对"}]
    edges = []
    review = res.get("immunology_review") if isinstance(res.get("immunology_review"), dict) else {}
    for i, ob in enumerate(review.get("objects") or [], start=1):
        if ob.get("status") == "not_reported" or ob.get("value") == "not_reported":
            continue
        pairs = evidence_for(ob.get("evidence_refs") or [])
        edges.append({
            "subject": ob.get("source", "not_reported"), "predicate": ob.get("arrow", "observed_relation"),
            "object": ob.get("target", "not_reported"), "polarity": "negated" if "not " in str(ob.get("conditions", "")).lower() else "asserted",
            "evidence_pairs": pairs, "scope": pairs[0].get("scope", "not_reported"),
            "comparison_targets": review.get("comparisons", "not_reported"),
            "decorrelated": False, "interpretation_completeness": ob.get("interpretation_completeness"),
            "causal_formal_support": False, "failure_notes": "跨层方向未被独立排除，不作为因果形式支持" if ob.get("relation_class") == "cross_layer_linkage" else "只保留原句关系，未增加上游中介",
        })
    cautions = res.get("caution_bindings") or []
    nodes = sorted({str(e["subject"]) for e in edges} | {str(e["object"]) for e in edges})
    res["teaching_graph"] = {
        "source_id": str(case_source_id(source_info)),
        "nodes": nodes or ["not_reported: 没有达到关系门槛的节点"],
        "open_caution_bindings": cautions,
        "interpretation_extension_notes": {
            "direct_binding_detection": "direct_binding_only_if_measured 必须含实验检测手段；其他一律人工",
            "upstream_dependency": "上游依赖需原文直接功能陈述，不由转录抑制或相关推得",
            "decorrelation_policy": "独立去混杂未直接测量时保守为 false，不以同向关系冒充独立因果",
            "number_condition_binding": "数值与适用条件共存在 evidence_pairs；跨开关比较留在 comparison_targets",
        },
        "edges": edges or [{"subject": "not_reported", "predicate": "not_reported", "object": "not_reported",
                            "polarity": "not_reported", "evidence_pairs": [{"reason": "没有满足门槛的科研关系"}],
                            "scope": "not_reported", "comparison_targets": "not_reported", "decorrelated": False,
                            "interpretation_completeness": "not_applicable", "causal_formal_support": False,
                            "failure_notes": "没有画出来自共现或猜想的边"}],
        "edge_count": len(edges), "node_count": len(nodes),
        "purpose": "人体与免疫教学使用的证据关系图；关系类型见 evidence_pairs 对应 claim.kind",
    }
    # Explicit eleven-key relation object, separate from display nodes for machine-independent inspection.
    res["teaching_relations"] = edges or [dict((k, "not_reported") for k in (
        "subject", "predicate", "object", "polarity", "evidence_pairs", "scope", "comparison_targets", "decorrelated",
        "interpretation_completeness", "causal_formal_support", "failure_notes"))]
    return res


def case_source_id(source_info):
    if isinstance(source_info, dict):
        dataset = source_info.get("dataset") if isinstance(source_info.get("dataset"), dict) else source_info
        files = dataset.get("source_files") or dataset.get("files") or []
        if files and isinstance(files, list):
            return files[0].get("source_id") or files[0].get("path") or "not_reported"
    return "not_reported"


def determine_status(result, focus_present, has_science, evidence_ok, artifacts_ok, requirements_complete=False):
    if result["status"] == "input_error":
        return "input_error"
    if focus_present and not has_science:
        return "insufficient_evidence"
    if not artifacts_ok:
        return "partial"
    if focus_present and has_science and evidence_ok and requirements_complete:
        return "answered"
    return "partial"


def main():
    parser = argparse.ArgumentParser(description="Single offline reading-client case")
    parser.add_argument("--input", default="/input/case.json")
    parser.add_argument("--output", default="/app/output")
    parser.add_argument("--workdir", default="/app/work")
    parser.add_argument("--seconds", type=float, default=float(os.environ.get("READER_INTERNAL_SECONDS", "105")),
                        help="internal soft deadline, below the 120-second hard target")
    args = parser.parse_args()
    start = time.monotonic()
    deadline = start + max(12, min(args.seconds, 108))
    case_path, outdir, workdir = Path(args.input).resolve(), Path(args.output).resolve(), Path(args.workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    outdir.mkdir(parents=True, exist_ok=True)
    trace = Trace(workdir, start)
    result = initial_result("not_reported")
    checkpoint(result, outdir, workdir, start, "start", "写入契约骨架；尚未宣称任何科学结论")
    stage_errors, source_info, deliverable_info = [], {}, {}
    case, case_id, input_error, input_sha = {}, "not_reported", None, None
    try:
        case, case_id, input_error, input_sha = load_case(case_path)
        result["case_id"] = case_id
        if input_error:
            result.update(status="input_error", result=nr(input_error),
                          uncertainties=[{"code": "input_error", "note": input_error}],
                          limitations=[{"kind": "input_error", "reason": input_error, "handoff": "修复 /input/case.json 后重新执行同一命令"}])
            checkpoint(result, outdir, workdir, start, "input_error", input_error)
            return finish_with_checks(result, case, outdir, workdir, trace, start, deadline)
        materials, material_route = resolve_materials(case, case_path)
        trace.log(f"case_id={case_id}; materials={materials}; route={material_route}; internal_deadline={args.seconds}s")
        remaining = deadline - time.monotonic()
        index_rc = trace.run("reader.py", ["index", materials, "-o", workdir / "docindex.json", "--jsonl", workdir / "blocks.jsonl"],
                             timeout=max(2, min(24, remaining - 70 if remaining > 74 else remaining - 2)))
        if index_rc["exit_code"] != 0 or not (workdir / "docindex.json").is_file():
            raise RuntimeError(f"reader.py 索引失败 exit={index_rc['exit_code']}: {index_rc['stderr'][-600:]}")
        index = load_json(workdir / "docindex.json")
        checkpoint(result, outdir, workdir, start, "index", f"真实索引 {index.get('doc_count', 0)} 份文档、{len(index.get('blocks', []))} 个块")
        remaining = deadline - time.monotonic()
        src_rc = trace.run("sources.py", ["build", "--case", case_path, "--index", workdir / "docindex.json", "--outdir", outdir],
                           timeout=max(1, min(12, remaining - 52 if remaining > 56 else remaining / 4)))
        if src_rc["exit_code"] == 0 and (outdir / "source_files.json").is_file():
            try:
                source_info = {"dataset": load_json(outdir / "source_files.json"),
                               "tables": load_json(outdir / "source_table_map.json") if (outdir / "source_table_map.json").is_file() else nr("未生成表映射"),
                               "report": load_json(outdir / "source_manifest.json") if (outdir / "source_manifest.json").is_file() else nr("来源报告未生成")}
            except (OSError, json.JSONDecodeError) as exc:
                stage_errors.append({"code": "source_report_unreadable", "visible_note": str(exc), "affected_paths": [str(outdir)], "manual_action": "检查来源 JSON"})
        else:
            stage_errors.append({"code": "source_mapping_failed", "visible_note": src_rc["stderr"][-1000:] or "来源映射未完成",
                                 "affected_paths": [str(materials)], "manual_action": "人工核对标题—表格映射，禁止任意配对"})
        import answer
        import evidence
        built = answer.build_answer(case, index)
        outer_claims = built["claims"]
        outer_uncertainties = built["uncertainties"]
        built = move_relation_payload(built, source_info)
        result["result"] = built
        result["claims"] = outer_claims
        result["uncertainties"] = outer_uncertainties + stage_errors
        result["limitations"] = [{"kind": "scope", "reason": "脚本只提取明确文本或原表观察；高阶因果、教学效果与跨范围外推均须人工复核",
                                  "handoff": "使用 human_review_manifest 定位每一项回原文"}]
        focus = answer._focus(case)
        focus_present = any(focus.values())
        has_science = bool(built["reading_scope"]["scientific_material_present"])
        scoped_keys = {"metabolism": ["metabolism_core", "metabolism_supplements"],
                       "enzyme": ["enzyme_study"], "immunology": ["immunology_review"]}
        scoped_values = [built[k] for group, keys in scoped_keys.items() if focus.get(group) for k in keys]
        requirements_complete = bool(scoped_values) and not any(required_missing(v) for v in scoped_values)
        result["source_manifest"] = relative_files(source_info or nr("来源构建失败；见 exception_notes"), outdir)
        checkpoint(result, outdir, workdir, start, "answer", "按真实索引构建字段、关系与人工复核清单；未将关键词命中视为回答")
        remaining = deadline - time.monotonic()
        doc_timeout = max(1, min(42, remaining - 11 if remaining > 15 else remaining / 2))
        doc_rc = trace.run("deliverables.py", ["build", "--result", outdir / "result.json", "--outdir", outdir, "--workdir", workdir],
                           timeout=doc_timeout)
        if (workdir / "deliverables_report.json").is_file():
            deliverable_info = load_json(workdir / "deliverables_report.json")
        else:
            deliverable_info = {"ok": False, "documents": [nr("文档生成脚本未返回报告")],
                                "exception_notes": [{"code": "document_timeout_or_failure", "visible_note": doc_rc["stderr"][-1200:],
                                                     "affected_paths": [str(outdir)], "manual_action": "检查 stage_log.json；预览未宣称成功"}]}
        result["artifact_manifest"] = relative_files(deliverable_info, outdir)
        doc_errors = deliverable_info.get("exception_notes") or []
        if isinstance(doc_errors, dict):
            doc_errors = [doc_errors]
        result["uncertainties"] += [x for x in doc_errors if isinstance(x, dict)]
        evidence_index = evidence.LocatorIndex(index)
        ev_records = []
        for i, claim in enumerate(result["claims"], start=1):
            quote = claim.get("evidence", {}).get("quote", "")
            if quote == "not_reported" or not quote:
                ev_records.append({"claim_index": i, "kind": claim.get("kind"), "exact": False, "found": False,
                                   "reason": "该记录只说明运行边界，不作为科学引语"})
                continue
            report = evidence.verify_quote(evidence_index, quote)
            claimed_loc = claim.get("location")
            report.update(claim_index=i, kind=claim.get("kind"), claimed_locator=claimed_loc,
                          locator_matches=report.get("locator") == claimed_loc)
            ev_records.append(report)
        evidence_ok = bool(ev_records) and all(x.get("exact") and x.get("locator_matches") for x in ev_records
                                               if x.get("kind") != "run_state")
        ev_payload = {"all_verified": evidence_ok, "checked": len(ev_records), "records": ev_records,
                      "rule": "exact quote AND claimed locator must match; non-scientific run_state excluded"}
        write_json(outdir / "evidence_verification.json", ev_payload)
        result["result"]["evidence_verification"] = {"status": "passed" if evidence_ok else "failed",
                                                      "report": "evidence_verification.json", "checked": len(ev_records)}
        artifacts_ok = bool(deliverable_info.get("ok")) and not any(
            isinstance(d, dict) and (d.get("docx") and not d.get("preview_ok"))
            for d in deliverable_info.get("documents", []))
        status = determine_status(result, focus_present, has_science, evidence_ok, artifacts_ok, requirements_complete)
        result["status"] = status
        if not evidence_ok:
            result["limitations"].append({"kind": "evidence", "reason": "至少一条引语没有同时通过 exact 与 locator 校验，或非科学运行边界占位",
                                          "handoff": "打开 evidence_verification.json；不删除失败记录"})
        if status != "answered":
            result["limitations"].append({"kind": "partial_answer" if status == "partial" else "insufficient_evidence",
                                          "reason": "存在 not_reported、材料缺失或关键预览/证据检查未全部通过",
                                          "handoff": "按各项 not_reported_reason 补原始材料，不用领域常识填空"})
        fulfill_custom_contract(result, case.get("output_contract") if isinstance(case.get("output_contract"), dict) else {})
        result["_meta"] = {"pipeline": "run_case.py", "schema": "reading-client-teaching/2", "input_sha256": input_sha,
                           "materials_root": str(materials), "material_route": material_route,
                           "elapsed_seconds": round(time.monotonic() - start, 3), "internal_budget_seconds": args.seconds,
                           "doc_count": index.get("doc_count", 0), "block_count": len(index.get("blocks", [])),
                           "claim_count": len(result["claims"]), "offline": True}
        checkpoint(result, outdir, workdir, start, "artifacts", f"状态 {status}；文档与预览报告已写入")
        return finish_with_checks(result, case, outdir, workdir, trace, start, deadline)
    except Exception as exc:
        debug = {"exception_type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc(),
                 "case_path": str(case_path), "output_dir": str(outdir), "timestamp_utc": now_iso()}
        try:
            write_json(workdir / "exception.json", debug)
        except OSError:
            pass
        trace.log(f"EXCEPTION {type(exc).__name__}: {exc}")
        result.setdefault("result", {})
        if not isinstance(result["result"], dict):
            result["result"] = nr("运行异常")
        result["result"].setdefault("reading_scope", nr("流水线中途异常，未读完材料"))
        result["result"]["exception_notes"] = [dict(code="unhandled_exception", visible_note=f"{type(exc).__name__}: {exc}",
                                                    affected_paths=[str(case_path)], manual_action="打开 /app/work/exception.json 与 stage_log.json")]
        result["status"] = "partial" if case and not input_error else "input_error"
        result["uncertainties"] = list(result.get("uncertainties") or []) + [result["result"]["exception_notes"][0]]
        result["limitations"] = list(result.get("limitations") or []) + [{"kind": "run_failure", "reason": debug["message"],
                                                                           "handoff": str(workdir / "exception.json")}]
        result.setdefault("_meta", {})["elapsed_seconds"] = round(time.monotonic() - start, 3)
        result.setdefault("_meta", {})["input_sha256"] = input_sha
        try:
            fulfill_custom_contract(result, case.get("output_contract") if isinstance(case.get("output_contract"), dict) else {})
        except Exception:
            pass
        checkpoint(result, outdir, workdir, start, "exception", f"{type(exc).__name__}: {exc}")
        return finish_with_checks(result, case, outdir, workdir, trace, start, deadline)


def finish_with_checks(result, case, outdir, workdir, trace, start, deadline):
    """Run transactional checks without allowing a validator to erase a partial result."""
    contract_path = workdir / "case.contract.json"
    try:
        write_json(contract_path, case if case else {"case_id": result.get("case_id"), "output_contract": {}})
        remaining = max(0.5, deadline - time.monotonic())
        check = trace.run("contract.py", ["check", outdir / "result.json", "--contract", contract_path,
                                           "--family", "reading"], timeout=min(5, remaining))
        try:
            report = json.loads(check["stdout"])
        except json.JSONDecodeError:
            report = {"problems": ["contract checker did not return JSON"], "notes": [check["stderr"][-500:]]}
        write_json(outdir / "contract_check.json", {"exit_code": check["exit_code"], **report})
        result["result"]["contract_compliance"] = {"report": "contract_check.json", "exit_code": check["exit_code"],
                                                   "problem_count": len(report.get("problems", []))}
        if check["exit_code"] != 0:
            result["status"] = "partial" if result.get("status") != "input_error" else "input_error"
            result.setdefault("limitations", []).append({"kind": "contract", "reason": "最终契约自检未通过",
                                                           "handoff": "查看 contract_check.json 的 problems；不得删除缺失字段"})
        remaining = max(0.5, deadline - time.monotonic())
        validation = trace.run("validate_case.py", ["check", "--result", outdir / "result.json", "--case", contract_path,
                                "--out", outdir / "self_check.json", "--skill-dir", SCRIPT_DIR.parent],
                               timeout=min(9, remaining))
        result["result"]["self_check_summary"] = {"report": "self_check.json", "exit_code": validation["exit_code"],
                                                  "note": "独立验证合同、证据定位、六维/十一键、来源清单与真实预览；不把 not_reported 当答案"}
        if validation["exit_code"] != 0:
            result["status"] = "partial" if result.get("status") != "input_error" else "input_error"
            result.setdefault("limitations", []).append({"kind": "artifact_or_relation_validation",
                                                          "reason": "独立结果检查发现未通过项",
                                                          "handoff": "查看 self_check.json 的 problems；预览与引文失败不得伪装成成功"})
        result.setdefault("_meta", {})["elapsed_seconds"] = round(time.monotonic() - start, 3)
        add_change(result, "validation", f"contract exit={check['exit_code']}; case checks exit={validation['exit_code']}", start)
        write_json(outdir / "result.json", result)
        write_json(outdir / "run_receipt.json", {
            "case_id": result.get("case_id"), "status": result.get("status"),
            "entry": "python3 /skills/weekly-skill/scripts/run_case.py",
            "actual_script_exit_code": 0, "elapsed_seconds": round(time.monotonic() - start, 3),
            "offline": True, "contract_exit_code": check["exit_code"], "validation_exit_code": validation["exit_code"],
            "result": "result.json", "stage_log": relative_files(str(workdir / "stage_log.json"), outdir),
        })
        trace.log(f"FINAL status={result['status']} elapsed={time.monotonic() - start:.2f}s")
    except Exception as exc:
        # A checking defect is exposed as metadata, not converted into a scientific answer.
        result.setdefault("limitations", []).append({"kind": "validation", "reason": f"验收步骤自身异常：{type(exc).__name__}: {exc}",
                                                    "handoff": str(workdir / "stage_log.json")})
        result["status"] = "partial" if result.get("status") != "input_error" else "input_error"
        write_json(outdir / "result.json", result)
        trace.log(f"CHECK EXCEPTION {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
