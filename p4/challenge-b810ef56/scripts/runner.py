"""Extracted finalize_result from run_case.py."""
from __future__ import annotations
import json, re, sys, time
from pathlib import Path
HERE = Path(__file__).parent

def finalize_result(args, case, case_path, output, work, spec, preview_info, claims, facts, sources, warnings,
                    verifications, authoring_problems, records, started, started_at, auto, authoring, contract_cmd,
                    status, total_numbers, preview_audit, logs, sha256, now_iso, nr, output_fields, save_result,
                    run_capture, write_json):
    change_log = list(authoring.get("change_log") or [])
    change_log += [
        {"action": "index_actual_material", "target": str(work/"blocks.json"),
         "reason": f"Indexed {max(0,len(sources)-1)} source(s), {len(spec['blocks'])} block(s).", "human_check": 0},
        {"action": "render_real_previews", "target": str(output / "preview"),
         "reason": f"Rendered via {preview_info['engine']} and audited PDF/PNG signatures.", "human_check": 0},
        {"action": "set_final_status", "target": str(output/"result.json"),
         "reason": f"Final status is {status} after contract, evidence, numeric, citation, claim-marker and preview checks.",
         "human_check": status != "answered"}
    ]
    limitations = list(authoring.get("limitations") or auto["limitations"])
    uncertainties = list(authoring.get("uncertainties") or [])
    if not uncertainties:
        uncertainties = [{"kind": "scientific_certainty", "value": "not_reported",
                          "reason": "Uncertainty is limited to explicit source scope and stated caveats unless the author supplied a sourced analysis."}]
    if total_numbers > len(facts) and not args.authoring:
        limitations.append({"kind": "numeric_ledger_truncated", "value": "not_reported",
                            "reason": f"The material contains {total_numbers} numeric tokens; the automatic ledger records at most 80 unique located values.",
                            "human_action": "Check remaining values before claiming exhaustive numeric preservation."})
    if not limitations:
        limitations = [{"kind": "known_limitations", "value": "not_reported",
                        "reason": "No unresolved limitation was supplied. This is not proof that none exists.",
                        "human_action": "Perform domain review before scientific use."}]
    result = {
        "case_id": case.get("case_id") if isinstance(case.get("case_id"), str) and case.get("case_id").strip() else "not_reported",
        "deliverable": {"path":str(output/"draft.md"),"format":"markdown","editable":True,
                        "kind":"evidence_writing","structure": [b.get("text") for b in spec["blocks"] if b.get("type") == "h1"] or ["Evidence status", "Claim-evidence ledger", "Limitations and human handoff"],
                        "editable_docx":preview_info["docx"],"previews":{"pdf":preview_info["pdf"],
                        "png":preview_info["png"],"engine":preview_info["engine"],"real_file_audit":preview_info["ok"],
                        "audit":preview_info["audit"]}},
        "preserved_facts":facts, "evidence_map":claims, "change_log":change_log, "status":status,
        "limitations":limitations, "uncertainties":uncertainties, "sources":sources,
        "warnings":warnings or [{"status":"not_reported","reason":"No warning."}],
        "output_contract":{"source":str(case_path),"fields":output_fields(case)},
        "verification":{"quote_checks":verifications or [{"status":"not_reported",
                          "reason": "No exact quote verification result is available; consult evidence_quotes.json for the reason."}],
                         "authoring_problems":authoring_problems or [{"status":"not_reported",
                          "reason":"No authoring problem; review required."}],
                         "commands":records},
        "runtime":{"started_at":started_at,"finished_at":now_iso(),"elapsed_seconds":round(time.monotonic()-started,3),
                    "max_seconds_argument":args.max_seconds,"within_120_seconds":(time.monotonic()-started)<120,
                    "network_access":"not_used","python":sys.version.split()[0],"case_sha256":sha256(case_path)}
    }
    draft_md = str(authoring.get("draft_markdown") or auto["draft_markdown"])
    oc_body = case.get("output_contract") or {}
    if isinstance(oc_body, dict) and "brief_sections" in oc_body and isinstance(oc_body["brief_sections"], dict):
        section_map: dict = {}
        expected_keys = list(oc_body["brief_sections"].keys())
        parts = re.split(r'\n##\s+', draft_md)
        for part in parts[1:]:
            heading_line, _, body = part.partition('\n')
            m = re.search(r'\((\w+)\)', heading_line)
            sec_key = m.group(1) if m else re.sub(r'\W+', '_', heading_line.strip()).strip('_').lower()
            if sec_key in expected_keys:
                section_map[sec_key] = body.strip()
        if section_map:
            result["brief_sections"] = {k: section_map.get(k, nr(f"Section {k} not found in draft.",
                                                                  str(case_path) + ":output_contract/brief_sections/" + k))
                                        for k in expected_keys}
    if isinstance(oc_body, dict) and "status_values" in oc_body:
        sv = oc_body["status_values"]
        result["status_values"] = sv if isinstance(sv, list) else [str(sv)]
    for key in output_fields(case):
        if key in result:
            continue
        supplied_values = authoring.get("contract_values") or {}
        result[key] = supplied_values.get(key) or nr(
            "No evidence-backed value was established for this case-specific contract field.",
            str(case_path) + ":output_contract/" + key)
    save_result(result, output, work)
    rec_contract = run_capture(contract_cmd, work, logs / "contract.log")
    result_cmd = [sys.executable, str(HERE / "text_checks.py"), "result", str(output/"result.json")]
    rec_result = run_capture(result_cmd, work, logs / "result.log")
    records.extend([rec_contract, rec_result])
    gate_failures = []
    if rec_contract["exit_code"] != 0 or rec_result["exit_code"] != 0:
        gate_failures.append("result_contract")
    if not preview_audit.get("ok"):
        gate_failures.append("real_preview")
    if gate_failures and status == "answered":
        status = "partial"
    result["status"] = status
    if gate_failures:
        result["limitations"].append({"kind": "final_gate_failure", "value": "not_reported",
                                      "reason": "Final gates failed: " + ", ".join(gate_failures),
                                      "human_action": "Read validation logs and repair the indicated artifact before use."})
    result["verification"]["commands"] = records
    result["runtime"]["finished_at"] = now_iso()
    result["runtime"]["elapsed_seconds"] = round(time.monotonic() - started, 3)
    result["runtime"]["within_120_seconds"] = (time.monotonic() - started) < 120
    save_result(result, output, work)
    rec_cf = run_capture(contract_cmd, work, logs / "contract-final.log")
    rec_rf = run_capture([sys.executable, str(HERE / "text_checks.py"), "result", str(output/"result.json")],
                         work, logs / "result-final.log")
    records.extend([rec_cf, rec_rf])
    result["verification"]["commands"] = records
    try:
        import summary_card
        summary = summary_card.render(str(output/"result.json"), str(output / "summary.png"))
    except Exception as exc:
        summary = {"png": None, "svg": None, "error": str(exc)[:200]}
    if not summary.get("png"):
        result["warnings"].append({"code": "summary_preview_unavailable", "reason": summary.get("error") or "No PNG was reported."})
    result["verification"]["commands"] = records
    result["runtime"]["finished_at"] = now_iso()
    result["runtime"]["elapsed_seconds"] = round(time.monotonic() - started, 3)
    result["runtime"]["within_120_seconds"] = (time.monotonic() - started) < 120
    save_result(result, output, work)
    write_json(work / "authoring_problems.json", {"problems": authoring_problems,
               "status": "not_reported" if not authoring_problems else "action_required"})
    write_json(work / "bootstrap.json", {"case": str(case_path), "case_sha256": sha256(case_path),
               "blocks": str(work/"blocks.json"), "writing_spec": str(work / "writing_spec.json"),
               "authoring_schema": {"draft_markdown": "string", "evidence_map": "non-empty list",
                                    "preserved_facts": "non-empty list", "change_log": "non-empty list",
                                    "limitations": "non-empty list", "requested_status": "answered only after all gates pass"}})
    files = []
    for p in sorted(output.rglob("*")):
        if p.is_file() and p.name != "manifest.json":
            files.append({"path": str(p.relative_to(output)), "bytes": p.stat().st_size, "sha256": sha256(p)})
    write_json(output / "manifest.json", {"case_id": result["case_id"], "status": result["status"],
               "files": files, "manifest_self_hash": "not_reported",
               "manifest_self_hash_reason": "A file cannot stably contain its own final SHA-256."})
    run_report = {"command": [sys.executable, str(Path(__file__).parent / "run_case.py")] + sys.argv[1:],
                  "exit_code": 0, "status": result["status"], "elapsed_seconds": result["runtime"]["elapsed_seconds"],
                  "case_id": result["case_id"], "result": str(output/"result.json"),
                  "manifest": str(output / "manifest.json"), "validation_directory": str(logs),
                  "records": records}
    write_json(work / "run_report.json", run_report)
    write_json(output / "run_report.json", run_report)
    print(json.dumps({"case_id": result["case_id"], "status": result["status"],
                      "elapsed_seconds": result["runtime"]["elapsed_seconds"],
                      "contract_exit": rec_cf["exit_code"], "result_exit": rec_rf["exit_code"],
                      "preview_ok": preview_audit.get("ok"), "result_path": str(output/"result.json"),
                      "manifest_path": str(output / "manifest.json")}, ensure_ascii=False, indent=2))
    return 0
