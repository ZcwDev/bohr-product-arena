#!/usr/bin/env python3
"""attach_meta — add the six file-metadata dimensions and a reproduction manifest to any skill's output.

Every knowledge-work brief in this contest scores the same completeness line: 文件类型 · 元数据字段 · 目录层级 ·
重复文件 · 版本差异 · 权限与来源, plus "换一个人能否照记录复跑". An engine that only writes its own answer leaves
that line empty, so this runs after the engine (the SKILL.md command runs both) and:

  inventory.tsv          path · type · bytes · sha256 · mtime · mode · depth · duplicate_of · version_chain · source
  repro_manifest.json    command · python · input digest · per-output sha256
  result.json            gains `metadata_coverage` (with explicit `missing`) and a `repro_manifest` pointer

It never rewrites the engine's own verdict: it only adds fields, and if `result.json` is absent it says so instead
of creating a fake one.

usage:
  attach_meta.py --input /input --output /app/output
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from _meta import attach as meta_attach
except Exception as exc:                              # pragma: no cover
    def meta_attach(inp, out):
        return {"unavailable": True, "reason": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    args = ap.parse_args()
    inp, out = Path(args.input), Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    dims = meta_attach(inp, out)
    res = out / "result.json"
    info = {"metadata_coverage": dims, "inventory": "inventory.tsv",
            "repro_manifest": (dims.get("reproduce") or {}).get("repro_manifest")}
    if res.is_file():
        try:
            payload = json.loads(res.read_text(encoding="utf-8"))
        except Exception:
            payload = None
        if isinstance(payload, dict):
            stage_files = [p2 for p2 in out.glob("*_result.json")]
            if stage_files:
                stages = payload.setdefault("stages", {})
                for sf in stage_files:
                    try:
                        stages[sf.stem.replace("_result", "")] = json.loads(sf.read_text(encoding="utf-8"))
                    except Exception:
                        continue
            # 题面「运行状态」一行要的是"成功 / 部分完成 / 无法判断 / 拒答 + 原因 + 可接管位置"，
            # 而机器字段 status 用的是 answered/partial/…：两套都给出，互不覆盖。
            STATE = {"answered": "成功", "spec_ready": "成功", "partial": "部分完成",
                     "needs_translation": "部分完成", "insufficient_evidence": "无法判断",
                     "input_error": "拒答", "no_translation_input": "部分完成"}
            st = str(payload.get("status"))
            payload["run_state"] = STATE.get(st, "部分完成")
            # 交接指针必须指向真存在的东西：以前 handover 写"见 gaps"，而 payload 里往往没有 gaps 键。
            payload.setdefault("gaps", [])
            if payload["run_state"] != "成功":
                reason = (payload.get("gaps") or payload.get("uncertainties") or ["见 result.json"])[:3]
                payload.setdefault("run_state_reason", reason)
                payload.setdefault("handover",
                                   "未满足项见 run_state_reason / gaps；材料齐备后重跑同一条命令即可接管")
                payload.setdefault("can_take_over_at",
                                   {"unmet": reason, "rerun": "python3 scripts/<entry>.py --input /input --output /app/output"})
            payload.setdefault("metadata_coverage", dims)
            payload.setdefault("inventory", "inventory.tsv")
            if dims.get("reproduce"):
                payload.setdefault("repro_manifest", dims["reproduce"].get("repro_manifest"))
                payload.setdefault("input_digest", dims["reproduce"].get("input_digest"))
            res.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
            info["merged_into"] = "result.json"
        else:
            info["result_json_unparsable"] = True
    else:
        info["result_json_missing"] = True
    print(json.dumps({"files": dims.get("files"), "duplicates": dims.get("duplicate_files", {}).get("groups"),
                      "version_chains": dims.get("version_differences", {}).get("chains"),
                      "missing_dimensions": dims.get("missing", []),
                      **{k: v for k, v in info.items() if k != "metadata_coverage"}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
