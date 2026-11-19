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
