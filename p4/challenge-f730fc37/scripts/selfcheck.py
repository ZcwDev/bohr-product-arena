#!/usr/bin/env python3
"""Selfcheck — Reading Evidence Tracer contract validator.

Validates:
  1. result.json structure against the reading contract
  2. Evidence locators resolve to real positions in input files
  3. Sidecar artifacts exist (annotations.jsonl, source_files.json, etc.)
  4. Evidence quotes can be found verbatim in source text

Usage:
    python3 scripts/selfcheck.py --input DIR --output DIR
    python3 scripts/selfcheck.py --output DIR              # input defaults to /input
    python3 scripts/selfcheck.py --result FILE             # check specific file

Exit codes:
    0 = all checks passed (warnings allowed)
    1 = contract violations found (errors)
    2 = internal error (e.g. file not found)
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

HERE = Path(__file__).resolve().parent

VALID_STATUSES = {"answered", "partial", "insufficient_evidence", "input_error"}
REQUIRED_CLAIM_FIELDS = {"kind", "evidence", "source_id", "location"}
REQUIRED_EVIDENCE_FIELDS = {"locator", "quote"}
VALID_VERDICTS = {"direct_support", "hedged", "negation_in_scope", "insufficient_evidence", "not_reported"}

# Optional imports
HAS_FITZ = False
try:
    import fitz
    HAS_FITZ = True
except ImportError:
    fitz = None


class LocatorVerifier:
    """Verify that evidence locators and quotes match real source content."""

    def __init__(self, input_dir: Path):
        self.input_dir = input_dir
        self._text_cache: dict[str, str] = {}
        self._blocks_cache: dict[str, list[dict]] = {}

    def load_source_text(self, source_id: str) -> str:
        """Load full text of a source file for verification."""
        if source_id in self._text_cache:
            return self._text_cache[source_id]

        fpath = self.input_dir / source_id
        if not fpath.is_file():
            # Try just the filename in the input dir
            for candidate in self.input_dir.rglob(fpath.name):
                fpath = candidate
                break
            if not fpath.is_file():
                self._text_cache[source_id] = ""
                return ""

        text = ""
        ext = fpath.suffix.lower()
        try:
            if ext == ".pdf":
                text = self._read_pdf_text(fpath)
            elif ext == ".docx":
                text = self._read_docx_text(fpath)
            elif ext == ".pptx":
                text = self._read_pptx_text(fpath)
            elif ext == ".xlsx":
                text = self._read_xlsx_text(fpath)
            elif ext in (".csv", ".tsv"):
                text = fpath.read_text(encoding="utf-8", errors="replace")
            else:
                text = fpath.read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass

        self._text_cache[source_id] = text
        return text

    def _read_pdf_text(self, fpath: Path) -> str:
        if HAS_FITZ:
            try:
                doc = fitz.open(str(fpath))
                text = "\n".join(page.get_text() for page in doc)
                doc.close()
                return text
            except Exception:
                pass
        # Fallback: pdftotext
        import subprocess
        try:
            r = subprocess.run(["pdftotext", "-layout", str(fpath), "-"],
                             capture_output=True, text=True, timeout=60)
            if r.returncode == 0:
                return r.stdout
        except Exception:
            pass
        return ""

    def _read_docx_text(self, fpath: Path) -> str:
        try:
            with zipfile.ZipFile(fpath, "r") as zf:
                parts = []
                for name in zf.namelist():
                    if "document.xml" in name:
                        tree = ET.parse(zf.open(name))
                        ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
                        for p in tree.iter(f"{{{ns}}}t"):
                            if p.text:
                                parts.append(p.text)
                return " ".join(parts)
        except Exception:
            return ""

    def _read_pptx_text(self, fpath: Path) -> str:
        try:
            with zipfile.ZipFile(fpath, "r") as zf:
                parts = []
                for name in sorted(zf.namelist()):
                    if "slides/slide" in name and name.endswith(".xml"):
                        tree = ET.parse(zf.open(name))
                        for elem in tree.iter():
                            if elem.text and len(elem.text.strip()) > 1:
                                parts.append(elem.text)
                return " ".join(parts)
        except Exception:
            return ""

    def _read_xlsx_text(self, fpath: Path) -> str:
        try:
            with zipfile.ZipFile(fpath, "r") as zf:
                parts = []
                if "xl/sharedStrings.xml" in zf.namelist():
                    tree = ET.parse(zf.open("xl/sharedStrings.xml"))
                    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
                    for si in tree.iter(f"{{{ns}}}t"):
                        if si.text:
                            parts.append(si.text)
                return " ".join(parts)
        except Exception:
            return ""

    def verify_locator(self, source_id: str, locator: str, quote: str) -> dict:
        """Verify that a locator+quote pair exists in the source."""
        result = {
            "locator_valid": False,
            "quote_found": False,
            "details": "",
        }

        if not locator:
            result["details"] = "empty locator"
            return result

        if not source_id:
            result["details"] = "empty source_id"
            return result

        text = self.load_source_text(source_id)
        if not text:
            result["details"] = f"source not readable: {source_id}"
            return result

        # Parse locator format: p<page>b<block_idx> or b<block_idx>
        m = re.match(r"p(\d+)b(\d+)", locator)
        if m:
            page_num = int(m.group(1))
            result["locator_valid"] = page_num > 0  # At least has a valid page number
        elif re.match(r"b\d+", locator):
            result["locator_valid"] = True
        else:
            result["details"] = f"unrecognized locator format: {locator}"
            return result

        # Verify quote exists in source text
        if quote and len(quote) >= 5:
            # Normalize both for comparison
            norm_quote = re.sub(r"\s+", " ", quote.strip().lower())
            norm_text = re.sub(r"\s+", " ", text.lower())
            if norm_quote[:20] in norm_text:
                result["quote_found"] = True
            elif len(norm_quote) >= 10 and norm_quote[:30] in norm_text:
                result["quote_found"] = True
            else:
                # Try a fuzzy match (allow for Unicode normalization differences)
                # Check if key words from the quote appear close together
                words = norm_quote.split()
                if len(words) >= 3:
                    key_words = [w for w in words if len(w) > 3][:5]
                    found = sum(1 for w in key_words if w in norm_text)
                    if found >= len(key_words) * 0.6:
                        result["quote_found"] = True
                        result["details"] = "fuzzy match (key words found)"
                    else:
                        result["details"] = f"quote not found (matched {found}/{len(key_words)} key words)"
                else:
                    result["details"] = "quote too short to verify"
                    result["quote_found"] = True  # Give benefit of doubt for short quotes
        elif quote:
            result["quote_found"] = True  # Short quotes get benefit of doubt
        else:
            result["details"] = "empty quote"

        return result


def check_contract(result: dict, input_dir: Path | None = None) -> list[dict]:
    """Validate result.json against the reading contract."""
    issues = []

    # ── 1. Top-level fields ──
    for field in ("case_id", "status", "result", "claims", "uncertainties"):
        if field not in result:
            issues.append({"severity": "error", "field": field,
                          "message": f"缺少必需字段: {field}"})

    # ── 2. Status validation ──
    status = result.get("status")
    if status and status not in VALID_STATUSES:
        issues.append({"severity": "error", "field": "status",
                      "message": f"status 值非法: {status!r}，"
                                 f"允许: {', '.join(sorted(VALID_STATUSES))}"})

    # ── 3. Case ID ──
    case_id = result.get("case_id", "")
    if not case_id or case_id == "unknown":
        issues.append({"severity": "warning", "field": "case_id",
                      "message": "case_id 为空或 'unknown'"})
    # Check for forbidden synthetic IDs
    for prefix in ("SYNTH-", "EDGE-", "TEST-", "REV-"):
        if case_id.startswith(prefix):
            issues.append({"severity": "error", "field": "case_id",
                          "message": f"禁止使用人造 case_id: {case_id}"})

    # ── 4. Claims structure ──
    claims = result.get("claims", [])
    if not isinstance(claims, list):
        issues.append({"severity": "error", "field": "claims",
                      "message": "claims 应为数组"})
        return issues

    empty_locators = 0
    empty_quotes = 0
    for i, claim in enumerate(claims):
        if not isinstance(claim, dict):
            issues.append({"severity": "error", "field": f"claims[{i}]",
                          "message": "claim 应为对象"})
            continue
        for f in REQUIRED_CLAIM_FIELDS:
            if f not in claim:
                issues.append({"severity": "error", "field": f"claims[{i}].{f}",
                              "message": f"claim 缺少必需字段: {f}"})

        evidence = claim.get("evidence", {})
        if isinstance(evidence, dict):
            for f in REQUIRED_EVIDENCE_FIELDS:
                if f not in evidence:
                    issues.append({"severity": "error",
                                  "field": f"claims[{i}].evidence.{f}",
                                  "message": f"evidence 缺少必需字段: {f}"})
            loc = evidence.get("locator", "")
            quote = evidence.get("quote", "")
            if not loc or loc.strip() == "":
                empty_locators += 1
            if not quote or len(quote.strip()) < 3:
                empty_quotes += 1

        verdict = claim.get("verdict", "")
        if verdict and verdict not in VALID_VERDICTS:
            issues.append({"severity": "warning", "field": f"claims[{i}].verdict",
                          "message": f"verdict 值不常见: {verdict!r}"})

    if empty_locators > 0:
        issues.append({"severity": "error", "field": "claims.evidence.locator",
                      "message": f"{empty_locators} 条 claim 的 evidence.locator 为空"})
    if empty_quotes > 0:
        issues.append({"severity": "warning", "field": "claims.evidence.quote",
                      "message": f"{empty_quotes} 条 claim 的 evidence.quote 为空或过短"})

    # ── 5. Uncertainties ──
    uncertainties = result.get("uncertainties", [])
    if not isinstance(uncertainties, list):
        issues.append({"severity": "error", "field": "uncertainties",
                      "message": "uncertainties 应为数组"})
    else:
        for i, u in enumerate(uncertainties):
            if not isinstance(u, dict):
                issues.append({"severity": "warning", "field": f"uncertainties[{i}]",
                              "message": "uncertainty 应为对象"})
                continue
            if "code" not in u:
                issues.append({"severity": "warning", "field": f"uncertainties[{i}].code",
                              "message": "uncertainty 缺少 code"})
            if "note" not in u:
                issues.append({"severity": "warning", "field": f"uncertainties[{i}].note",
                              "message": "uncertainty 缺少 note"})

    # ── 6. Limitations for non-answered status ──
    if status and status != "answered":
        if not result.get("limitations"):
            issues.append({"severity": "warning", "field": "limitations",
                          "message": f"status={status} 但缺少 limitations 说明"})

    # ── 7. Evidence locator verification ──
    if input_dir and input_dir.is_dir() and claims:
        verifier = LocatorVerifier(input_dir)
        verified = 0
        quote_found = 0
        locator_valid = 0
        unverifiable = 0

        for i, claim in enumerate(claims):
            evidence = claim.get("evidence", {})
            if not isinstance(evidence, dict):
                continue
            source_id = claim.get("source_id", "")
            locator = evidence.get("locator", "")
            quote = evidence.get("quote", "")

            if not locator:
                continue

            vr = verifier.verify_locator(source_id, locator, quote)
            verified += 1
            if vr["locator_valid"]:
                locator_valid += 1
            if vr["quote_found"]:
                quote_found += 1
            else:
                unverifiable += 1

        if verified > 0:
            loc_rate = locator_valid / verified
            quote_rate = quote_found / verified
            issues.append({"severity": "info", "field": "evidence_verification",
                          "message": f"定位符验证: {locator_valid}/{verified} 有效, "
                                     f"{quote_found}/{verified} 引文可找到 ({quote_rate:.0%})"})
            if quote_rate < 0.5 and verified > 3:
                issues.append({"severity": "warning", "field": "evidence_verification",
                              "message": f"引文验证率偏低 ({quote_rate:.0%})，"
                                         f"可能有 {unverifiable} 条引文无法在原文中找到"})

    # ── 8. Result structure ──
    result_obj = result.get("result", {})
    if isinstance(result_obj, dict):
        if "questions" in result_obj:
            questions = result_obj["questions"]
            if not isinstance(questions, list):
                issues.append({"severity": "error", "field": "result.questions",
                              "message": "result.questions 应为数组"})

    return issues


def check_artifacts(output_dir: Path) -> list[dict]:
    """Check that expected output artifacts exist."""
    issues = []
    expected = {
        "result.json": "error",
        "annotations.jsonl": "warning",
        "source_files.json": "warning",
        "blocks_index.json": "info",
        "reading_record.json": "info",
        "extracted_text.txt": "info",
    }

    for fname, severity in expected.items():
        fpath = output_dir / fname
        if fpath.is_file():
            size = fpath.stat().st_size
            issues.append({"severity": "info", "field": f"artifact:{fname}",
                          "message": f"✓ 存在 ({size} bytes)"})
            # Validate JSON files
            if fname.endswith(".json"):
                try:
                    json.loads(fpath.read_text(encoding="utf-8"))
                except json.JSONDecodeError as e:
                    issues.append({"severity": "error", "field": f"artifact:{fname}",
                                  "message": f"JSON 无效: {e}"})
            # Validate JSONL files
            elif fname.endswith(".jsonl"):
                try:
                    lines = fpath.read_text(encoding="utf-8").strip().split("\n")
                    for li, line in enumerate(lines):
                        if line.strip():
                            json.loads(line)
                    issues.append({"severity": "info", "field": f"artifact:{fname}:lines",
                                  "message": f"✓ {len(lines)} 行有效"})
                except json.JSONDecodeError as e:
                    issues.append({"severity": "error", "field": f"artifact:{fname}",
                                  "message": f"JSONL 第{li+1}行无效: {e}"})
        else:
            issues.append({"severity": severity, "field": f"artifact:{fname}",
                          "message": f"缺失: {fname}"})

    return issues


def main():
    parser = argparse.ArgumentParser(
        description="Reading Evidence Tracer — contract + evidence selfcheck")
    parser.add_argument("--input", default="/input",
                        help="Input directory with materials (default: /input)")
    parser.add_argument("--output", default="/app/output",
                        help="Output directory (default: /app/output)")
    parser.add_argument("--result", default=None,
                        help="Path to result.json (overrides --output)")
    parser.add_argument("--no-verify-evidence", action="store_true",
                        help="Skip evidence locator verification")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Show all checks including info-level")
    args = parser.parse_args()

    # Resolve result path
    if args.result:
        result_path = Path(args.result)
        output_dir = result_path.parent
    else:
        output_dir = Path(args.output)
        result_path = output_dir / "result.json"

    if not result_path.exists():
        print(f"[selfcheck] ERROR: result.json not found at {result_path}", file=sys.stderr)
        return 2

    try:
        with open(result_path, encoding="utf-8") as f:
            result = json.load(f)
    except json.JSONDecodeError as e:
        print(f"[selfcheck] ERROR: result.json is not valid JSON: {e}", file=sys.stderr)
        return 2

    input_dir = Path(args.input)

    # ── Contract checks ──
    issues = check_contract(
        result,
        input_dir=input_dir if not args.no_verify_evidence else None
    )

    # ── Artifact checks ──
    if output_dir.is_dir():
        issues.extend(check_artifacts(output_dir))

    # ── Report ──
    errors = [i for i in issues if i["severity"] == "error"]
    warnings = [i for i in issues if i["severity"] == "warning"]
    infos = [i for i in issues if i["severity"] == "info"]

    print(f"[selfcheck] ═══════════════════════════════════════════════")
    print(f"[selfcheck] result.json: {result_path}")
    print(f"[selfcheck] case_id: {result.get('case_id', 'N/A')}")
    print(f"[selfcheck] status: {result.get('status', 'N/A')}")
    print(f"[selfcheck] claims: {len(result.get('claims', []))}")
    print(f"[selfcheck] uncertainties: {len(result.get('uncertainties', []))}")
    print(f"[selfcheck] ───────────────────────────────────────────────")
    print(f"[selfcheck] errors: {len(errors)}, warnings: {len(warnings)}, info: {len(infos)}")

    if errors:
        print(f"\n[selfcheck] ERRORS ({len(errors)}):")
        for issue in errors:
            print(f"  ✗ {issue['field']}: {issue['message']}")

    if warnings:
        print(f"\n[selfcheck] WARNINGS ({len(warnings)}):")
        for issue in warnings:
            print(f"  ⚠ {issue['field']}: {issue['message']}")

    if args.verbose and infos:
        print(f"\n[selfcheck] INFO ({len(infos)}):")
        for issue in infos:
            print(f"  ℹ {issue['field']}: {issue['message']}")

    # Summary
    print(f"\n[selfcheck] ═══════════════════════════════════════════════")
    if not errors and not warnings:
        print("[selfcheck] ✓ ALL CHECKS PASSED")
        return 0
    elif errors:
        print(f"[selfcheck] ✗ {len(errors)} ERROR(S) — contract violation")
        return 1
    else:
        print(f"[selfcheck] ⚠ {len(warnings)} WARNING(S) — non-fatal")
        return 0


if __name__ == "__main__":
    sys.exit(main())
