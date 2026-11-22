#!/usr/bin/env python3
"""Text-level checks for the reading/writing challenges.

Subcommands
  numbers   diff numeric/unit/reference-marker tokens between two texts (original vs revised/translated)
  terms     build a glossary from a bilingual pair, then report terms that violate it
  citations audit citation markers: placement, numbering, list/body agreement, duplicates, missing sources
  dedup     find duplicate reference entries (preprint/published, EN/CN title, same DOI)
  claims    split prose into claims and report which lack a supporting marker
  result    validate result.json against the shared output contract (fields, evidence, honest status)

All checks are deterministic and print machine-readable JSON so the agent can attach evidence.
Nothing here invents content: a failure is reported, never repaired silently.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys

NUM_RE = re.compile(r"(?<![\w.])([-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][-+]?\d+)?"
                    r"(?:\s?[×x]\s?10\s?\^?[-+]?\d+)?(?:\s?[%‰])?)(?![\w])")
# a unit counts only when it is attached to a number: "42.5 mg/L" yes, "the yield" no
UNIT_AFTER_RE = re.compile(r"(?<=\d)\s?(%|‰|°[CF]|Å|Ω|[a-zA-Zµμ][a-zA-Z0-9µμ/·^*_−-]{0,11})"
                           r"(?![\w])")
CITE_RE = re.compile(r"\[(\d+(?:\s*[-–,，]\s*\d+)*)\]|\\cite[tp]?\{([^}]+)\}|\(([A-Z][A-Za-z\-]+(?: et al\.?)?,\s*\d{4}[a-z]?)\)")
MARKER_RE = re.compile(r"\[\d+(?:\s*[-–,，]\s*\d+)*\]|\\cite[tp]?\{[^}]+\}|\([A-Z][A-Za-z\-]+(?: et al\.?)?,\s*\d{4}[a-z]?\)|\[\^?\d+\]|【\d+】")
REF_LINE_RE = re.compile(r"^\s*(?:\[\d+\]|\d+\.|\\bibitem\{[^}]+\}|[-*]\s*§?\d*\.?)\s*(.{20,})$")
SENT_SPLIT = re.compile(r"(?<=[。！？!?；;])|(?<=\.)\s+(?=[A-Z\u4e00-\u9fff])")
DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")


CITE_MARK_RE = re.compile(r"\[\d+(?:\s*[-–,，]\s*\d+)*\]|【\d+】|\\cite[tp]?\{[^}]+\}")


def _tokens(text):
    # citation markers carry their own check (markers_lost/added); drop them here so
    # "[1]" is not double-reported as the bare number 1
    bare = CITE_MARK_RE.sub(" ", text)
    return [m.group(1).strip() for m in NUM_RE.finditer(bare)]


def cmd_numbers(args):
    a = open(args.a, encoding="utf-8", errors="replace").read()
    b = open(args.b, encoding="utf-8", errors="replace").read()
    ta, tb = _tokens(a), _tokens(b)
    from collections import Counter
    ca, cb = Counter(ta), Counter(tb)
    missing = {k: v for k, v in ca.items() if cb.get(k, 0) < v}
    added = {k: v for k, v in cb.items() if ca.get(k, 0) < v}
    ma = sorted(set(m.group(0) for m in MARKER_RE.finditer(a)))
    mb = sorted(set(m.group(0) for m in MARKER_RE.finditer(b)))
    out = {"numbers_in_a": len(ta), "numbers_in_b": len(tb),
           "numbers_missing_in_b": missing, "numbers_added_in_b": added,
           "markers_lost": [m for m in ma if m not in mb],
           "markers_added": [m for m in mb if m not in ma],
           "units_a": sorted(set(UNIT_AFTER_RE.findall(a))), "units_b": sorted(set(UNIT_AFTER_RE.findall(b)))}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if not missing and not out["markers_lost"] else 1


def cmd_terms(args):
    pairs = json.load(open(args.pairs, encoding="utf-8"))  # [{"src": "...", "dst": "..."}]
    out = {"violations": [], "glossary": {}}
    for pr in pairs:
        src, dst = pr["src"], pr["dst"]
        for term, allowed in (pr.get("glossary") or {}).items():
            if term.lower() in src.lower():
                allowed = [allowed] if isinstance(allowed, str) else allowed
                if not any(a in dst for a in allowed):
                    out["violations"].append({"term": term, "expected": allowed,
                                              "segment": pr.get("id"), "found": dst[:120]})
        out["glossary"].update(pr.get("glossary") or {})
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if not out["violations"] else 1


def _cite_numbers(text):
    nums = []
    for m in CITE_RE.finditer(text):
        if m.group(1):
            for part in re.split(r"\s*[,，]\s*", m.group(1)):
                if re.search(r"[-–]", part):
                    lo, hi = [int(x) for x in re.split(r"\s*[-–]\s*", part)[:2]]
                    if lo <= hi and hi - lo < 60:
                        nums.extend(range(lo, hi + 1))
                elif part.strip().isdigit():
                    nums.append(int(part))
    return nums


def cmd_citations(args):
    text = open(args.text, encoding="utf-8", errors="replace").read()
    body, refs = text, ""
    m = list(re.finditer(r"(?im)^#{1,6}\s*(references|参考文献|bibliography)\s*$", text))
    if m:
        body, refs = text[:m[-1].start()], text[m[-1].end():]
    listed = {}
    for line in refs.splitlines():
        mm = re.match(r"^\s*\[(\d+)\]\s*(.+)$", line)
        if mm:
            listed[int(mm.group(1))] = mm.group(2).strip()
    cited = _cite_numbers(body)
    unlisted = sorted({n for n in cited if n not in listed})
    uncited = sorted({n for n in listed if n not in cited})
    sentences = [s.strip() for s in SENT_SPLIT.split(body) if len(re.sub(r"\W", "", s.strip())) >= 12]
    uncited_sentences = [s[:140] for s in sentences if not MARKER_RE.search(s)]
    coverage = 1 - len(uncited_sentences) / max(1, len(sentences))
    out = {"sentences": len(sentences), "coverage": round(coverage, 3),
           "cited_numbers": sorted(set(cited)), "listed": len(listed),
           "cited_but_not_listed": unlisted, "listed_but_not_cited": uncited,
           "uncited_sentences": uncited_sentences[:40],
           "duplicate_numbers": sorted({n for n in set(cited) if cited.count(n) > 3})}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if not unlisted and coverage >= 0.85 else 1


def cmd_dedup(args):
    entries = json.load(open(args.refs, encoding="utf-8"))
    entries = entries.get("entries", entries) if isinstance(entries, dict) else entries
    norm = []
    for e in entries:
        t = e.get("text") if isinstance(e, dict) else str(e)
        norm.append({"text": t, "doi": (DOI_RE.search(t or "") or [None])[0] if DOI_RE.search(t or "") else None,
                     "key": re.sub(r"[^a-z0-9]+", "", (t or "").lower())[:120]})
    dups = []
    for i in range(len(norm)):
        for j in range(i + 1, len(norm)):
            a, b = norm[i], norm[j]
            if a["doi"] and a["doi"] == b["doi"]:
                dups.append({"type": "same_doi", "i": i, "j": j, "doi": a["doi"]}); continue
            r = difflib.SequenceMatcher(None, a["key"], b["key"]).ratio()
            if r > 0.9:
                dups.append({"type": "near_duplicate", "i": i, "j": j, "ratio": round(r, 3),
                             "a": a["text"][:100], "b": b["text"][:100]})
    print(json.dumps({"n": len(norm), "duplicates": dups[:50], "n_duplicates": len(dups)},
                     ensure_ascii=False, indent=1))
    return 0 if not dups else 1


def cmd_claims(args):
    text = open(args.text, encoding="utf-8", errors="replace").read()
    out = []
    for para in re.split(r"\n\s*\n", text):
        for s in SENT_SPLIT.split(para):
            s = s.strip()
            if len(re.sub(r"\W", "", s)) < 15 or s.startswith("#"):
                continue
            out.append({"claim": s[:180], "supported": bool(MARKER_RE.search(s))})
    unsupported = [c for c in out if not c["supported"]]
    print(json.dumps({"claims": len(out), "unsupported": len(unsupported),
                      "unsupported_list": unsupported[:40]}, ensure_ascii=False, indent=1))
    return 0 if not unsupported else 1


def cmd_result(args):
    # delegate to contract.py: a second, differently-shaped field list here would
    # contradict the contract checker and flag valid reading results as broken
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import contract as contract_mod

    data = json.load(open(args.path, encoding="utf-8"))
    rep = contract_mod.check(data, None, None)
    problems = list(rep["problems"])
    blob = json.dumps(data, ensure_ascii=False)
    if re.search(r"(TODO|FIXME|PLACEHOLDER|待补)", blob):
        problems.append("placeholder text left in the deliverable")
    print(json.dumps({"file": args.path, "family": rep["family"], "fields": sorted(data.keys()),
                      "problems": problems, "notes": rep["notes"]}, ensure_ascii=False, indent=1))
    return 0 if not problems else 1


def main():
    ap = argparse.ArgumentParser(description="deterministic text checks with JSON output")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("numbers"); p.add_argument("a"); p.add_argument("b"); p.set_defaults(fn=cmd_numbers)
    p = sub.add_parser("terms"); p.add_argument("pairs"); p.set_defaults(fn=cmd_terms)
    p = sub.add_parser("citations"); p.add_argument("text"); p.set_defaults(fn=cmd_citations)
    p = sub.add_parser("dedup"); p.add_argument("refs"); p.set_defaults(fn=cmd_dedup)
    p = sub.add_parser("claims"); p.add_argument("text"); p.set_defaults(fn=cmd_claims)
    p = sub.add_parser("result"); p.add_argument("path"); p.set_defaults(fn=cmd_result)
    a = ap.parse_args()
    sys.exit(a.fn(a))


if __name__ == "__main__":
    main()
