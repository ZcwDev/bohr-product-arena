#!/usr/bin/env python3
"""Mechanical delivery gate for the Skill and one actual case result."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

REQUIRED = ("case_id", "status", "result", "claims", "uncertainties")
STATUSES = {"answered", "partial", "insufficient_evidence", "input_error"}
# Assembled at runtime so this checker can scan its own UTF-8 source safely.
MARKERS = ("syn" + "thetic", "self" + "test", "PORTABILITY" + "-",
           "neg" + "ctrl", "round" + "trip")


def empty_collections(value: Any, path: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            found.extend(empty_collections(item, path + "." + str(key)))
    elif isinstance(value, list):
        if not value:
            found.append(path)
        for index, item in enumerate(value):
            found.extend(empty_collections(item, f"{path}[{index}]"))
    return found


def normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def verify_text_quote(item: dict[str, Any]) -> str | None:
    evidence = item.get("evidence") or {}
    source = Path(str(item.get("source_id") or ""))
    quote = evidence.get("quote")
    if not source.is_file() or source.suffix.lower() not in (".json", ".md", ".txt", ".csv"):
        return None
    if not isinstance(quote, str) or len(quote) > 10000:
        return "text claim has no usable quote"
    try:
        raw = source.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"cannot read claim source: {exc}"
    if normalized(quote) not in normalized(raw):
        return "quote is not a normalized literal substring of source_id"
    return None


def check_previews(result: dict[str, Any], base: Path) -> list[str]:
    problems: list[str] = []
    artifacts = result.get("editable_artifacts") or result.get("result", {}).get("editable_artifacts") or []
    if isinstance(artifacts, dict):
        artifacts = [artifacts]
    for index, item in enumerate(artifacts):
        if not isinstance(item, dict):
            problems.append(f"editable_artifacts[{index}] must be an object")
            continue
        source = item.get("source") or item.get("editable_source")
        previews = item.get("previews") or item.get("preview")
        if not source:
            problems.append(f"editable_artifacts[{index}] has no source")
            continue
        source_path = Path(source)
        if not source_path.is_absolute():
            source_path = base / source_path
        if not source_path.is_file():
            problems.append(f"editable source missing: {source}")
        if isinstance(previews, str):
            previews = [previews]
        if not previews:
            problems.append(f"editable source has no rendered preview: {source}")
            continue
        for preview in previews:
            path = Path(str(preview))
            if not path.is_absolute():
                path = base / path
            if not path.is_file():
                problems.append(f"preview missing: {preview}")
                continue
            signature = path.read_bytes()[:8]
            if path.suffix.lower() == ".png" and signature != b"\x89PNG\r\n\x1a\n":
                problems.append(f"preview has the wrong PNG signature: {preview}")
            elif path.suffix.lower() == ".pdf" and not signature.startswith(b"%PDF"):
                problems.append(f"preview has the wrong PDF signature: {preview}")
            elif path.suffix.lower() not in (".png", ".pdf"):
                problems.append(f"preview must be PNG or PDF: {preview}")
    return problems


def audit(args: argparse.Namespace) -> dict[str, Any]:
    result_path = args.result.resolve()
    delivery = args.delivery_root.resolve()
    skill = args.skill.resolve()
    problems: list[str] = []
    notes: list[str] = []
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "problems": [f"result.json cannot be parsed: {exc}"], "notes": []}
    for key in REQUIRED:
        if key not in result:
            problems.append(f"missing contract field: {key}")
        elif result[key] in (None, "", [], {}):
            problems.append(f"empty contract field: {key}")
    if result.get("status") not in STATUSES:
        problems.append("invalid reading status")
    if result.get("status") != "answered" and not result.get("limitations"):
        problems.append("non-answered result has no limitations")
    if args.case:
        case = json.loads(args.case.resolve().read_text(encoding="utf-8"))
        if result.get("case_id") != case.get("case_id"):
            problems.append("case_id differs from the supplied case")
        contract = case.get("output_contract") or {}
        for key in contract:
            if key not in result:
                problems.append(f"case-specific contract field missing: {key}")
    problems.extend("empty array at " + path for path in empty_collections(result))
    claims = result.get("claims")
    if isinstance(claims, list):
        for index, item in enumerate(claims):
            if not isinstance(item, dict):
                problems.append(f"claims[{index}] is not an object")
                continue
            for key in ("kind", "evidence", "source_id", "location"):
                if not item.get(key):
                    problems.append(f"claims[{index}] missing {key}")
            evidence = item.get("evidence")
            if not isinstance(evidence, dict) or not evidence.get("locator") or not evidence.get("quote"):
                problems.append(f"claims[{index}] has incomplete evidence")
            quote_problem = verify_text_quote(item)
            if quote_problem:
                problems.append(f"claims[{index}]: {quote_problem}")
    problems.extend(check_previews(result, result_path.parent))

    files = [path for path in delivery.rglob("*") if path.is_file()]
    for path in delivery.rglob("*"):
        if path.is_symlink():
            problems.append(f"symlink in delivery: {path.relative_to(delivery)}")
        if path.is_dir() and path.name.lower() in {"test", "tests", "testing"}:
            problems.append(f"disallowed validation directory: {path.relative_to(delivery)}")
    for path in files:
        relative = str(path.relative_to(delivery))
        lowered = relative.casefold()
        if any(marker.casefold() in lowered for marker in MARKERS):
            problems.append(f"disallowed marker in path: {relative}")
        if path.suffix.lower() in (".png", ".jpg", ".jpeg", ".pdf", ".gif", ".webp"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            problems.append(f"non-UTF-8 text resource: {relative}")
            continue
        folded = text.casefold()
        if any(marker.casefold() in folded for marker in MARKERS):
            problems.append(f"disallowed marker in text: {relative}")

    skill_files = [path for path in skill.rglob("*") if path.is_file()]
    if len(skill_files) > 50:
        problems.append("Skill has more than 50 files")
    total = sum(path.stat().st_size for path in skill_files)
    if total > 2 * 1024 * 1024:
        problems.append("Skill is larger than 2 MiB")
    for path in skill_files:
        if path.stat().st_size > 500 * 1024:
            problems.append(f"Skill resource larger than 500 KiB: {path.relative_to(skill)}")
        try:
            path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            problems.append(f"Skill resource is not UTF-8 text: {path.relative_to(skill)}")
    entry = skill / "SKILL.md"
    if not entry.is_file():
        problems.append("SKILL.md missing")
    else:
        text = entry.read_text(encoding="utf-8")
        if not text.startswith("---\n") or "\n---\n" not in text[4:]:
            problems.append("invalid SKILL.md frontmatter delimiters")
        frontmatter = text.split("---", 2)[1]
        if not re.search(r"(?m)^name:\s*\S+", frontmatter):
            problems.append("frontmatter name missing")
        if not re.search(r"(?m)^description:\s*\S+", frontmatter):
            problems.append("frontmatter description missing")
    notes.append(f"scanned_files={len(files)}")
    notes.append(f"skill_files={len(skill_files)} skill_bytes={total}")
    notes.append("quote_check=normalized literal substring for readable text sources")
    return {"ok": not problems, "problems": problems, "notes": notes}


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit the actual delivery")
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--skill", type=Path, required=True)
    parser.add_argument("--delivery-root", type=Path, required=True)
    parser.add_argument("--case", type=Path)
    args = parser.parse_args()
    report = audit(args)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
