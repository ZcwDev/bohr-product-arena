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
import re
import sys

NUM_RE = re.compile(r"(?<![\w])(\d+(?:[.,]\d+)?(?:\s?[×x]\s?10\s?\^?-?\d+)?(?:\s?%)?)(?![\w])")

# Units only, never "any 1-6 letter word": a bare [A-Za-z]{1,6} alternation reports
# ordinary prose ("After", "Bars", "DMEM", "Figure") as a unit and buries the real
# unit drift this check exists to catch. Longer units come first so that "mm"
# wins over "m" and "mM" over "M".
UNIT_LIST = [
    "kcal", "kDa", "kPa", "kHz", "kJ", "kW", "kbar", "kbp",
    "ppm", "ppb", "rpm", "cpm", "mol", "mmol", "µmol", "μmol", "nmol", "pmol",
    "mg", "µg", "μg", "ng", "pg", "kg", "mL", "dL", "µL", "μL", "nL", "pL",
    "cm", "mm", "µm", "μm", "um", "nm", "km", "mb", "kb", "Da", "bp",
    "ms", "min", "hr", "Hz", "MHz", "GHz", "mM", "µM", "μM", "nM", "pM",
    "Pa", "MPa", "mV", "kV", "mA", "µA", "μA", "mW", "MW",
    "mL/min", "mg/kg", "mg/L", "g/L", "µg/mL", "μg/mL", "ng/mL", "mM/L",
    "%", "‰", "°C", "°F", "Å", "Ω",
    # single letters are also ordinary words/initials ("Fixture A", "J Synth Biol"),
    # so they only count when attached to a number - see WEAK_UNIT_RE
    "M", "N", "V", "A", "W", "J", "g", "L", "m", "s", "h", "d", "K", "x", "×",
]
WEAK_UNITS = {"M", "N", "V", "A", "W", "J", "g", "L", "m", "s", "h", "d", "K", "x", "×"}
_STRONG = [u for u in UNIT_LIST if u not in WEAK_UNITS]
WEAK_LIST = [u for u in UNIT_LIST if u in WEAK_UNITS]
_STRONG_ALT = "|".join(re.escape(u) for u in sorted(set(_STRONG), key=len, reverse=True))
_WEAK_ALT = "|".join(re.escape(u) for u in sorted(set(WEAK_LIST), key=len, reverse=True))
STRONG_UNIT_RE = re.compile(rf"(?<![\w])({_STRONG_ALT})(?![\w])")
WEAK_UNIT_RE = re.compile(rf"(?<=\d)\s?({_WEAK_ALT})(?![\w])")


class _UnitFinder:
    """Union of strong matches and number-anchored weak matches, in text order."""

    @staticmethod
    def findall(text: str) -> list[str]:
        found = []
        for m in STRONG_UNIT_RE.finditer(text):
            found.append((m.start(), m.group(1)))
        for m in WEAK_UNIT_RE.finditer(text):
            found.append((m.start(1), m.group(1)))
        found.sort()
        return [u for _, u in found]


UNIT_RE = _UnitFinder
CITE_RE = re.compile(r"\[(\d+(?:\s*[-–,，]\s*\d+)*)\]|\\cite[tp]?\{([^}]+)\}|\(([A-Z][A-Za-z\-]+(?: et al\.?)?,\s*\d{4}[a-z]?)\)")
# Locator citations are this skill's own house style for a PDF (`[p1b4]`) and for
# a plain-text/code source (`[notes.md:12]`). They must count as a support marker,
# otherwise `claims` reports every properly sourced sentence as unsupported -
# exactly inverting what the check is for.
MARKER_RE = re.compile(r"\[\d+(?:\s*[-–,，]\s*\d+)*\]|\\cite[tp]?\{[^}]+\}|"
                       r"\([A-Z][A-Za-z\-]+(?: et al\.?)?,\s*\d{4}[a-z]?\)|\[\^?\d+\]|【\d+】|"
                       r"\[p\d+b\d+(?:[-.]\d+)?\]|\[[A-Za-z0-9._/-]+:\d+(?:-\d+)?\]")
REF_LINE_RE = re.compile(r"^\s*(?:\[\d+\]|\d+\.|\\bibitem\{[^}]+\}|[-*]\s*§?\d*\.?)\s*(.{20,})$")
SENT_SPLIT = re.compile(r"(?<=[。！？!?；;])|(?<=\.)\s+(?=[A-Z\u4e00-\u9fff])")
DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")


# A number in prose is not the same object as a section number, a citation
# marker, a page marker or a volume/page range inside a reference entry. Treating
# all four alike made this check fail on any draft that uses headings or cites
# anything - e.g. "## 1. ..." reported as a lost value "1", and the DOI
# "10.0000/fixture.0001" reported as two lost numbers. Only the first kind is
# value drift; the rest are structure the draft is free to re-lay-out.
_STRUCT_RES = [
    (re.compile(r"<!--\s*page\s+\d+\s*-->", re.I), "page_marker"),
    (re.compile(r"\[[^\]\n]{0,40}\]"), "citation_marker"),
    (re.compile(r"【[^】\n]{0,20}】"), "citation_marker"),
    (re.compile(r"\\cite[tp]?\{[^}\n]*\}"), "citation_marker"),
    (re.compile(r"^\s*#{1,6}\s*\d+(?:\.\d+)*[.)]?\s", re.M), "section_number"),
    (re.compile(r"^\s*\d+(?:\.\d+)*[.)]\s+(?=[A-Z\u4e00-\u9fff])", re.M), "section_number"),
]
_BIBLIO_RES = [
    # The suffix is optional: a draft that talks *about* a DOI ("the prefix
    # `10.0000/` is a placeholder") has no registrant code after the slash, and
    # requiring one let that fragment fall through as a substantive value.
    re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]*"),      # DOI, kept whole
    re.compile(r"(?im)^\s*\[\d+\]\s.*$"),                  # numbered reference entry
    re.compile(r"(?im)^\s*\d+\.\s+[A-Z][^.\n]{20,}$"),     # numbered reference entry
]


def _spans(regex_list, text):
    out = []
    for rx in regex_list:
        out.extend((m.start(), m.end()) for m in rx.finditer(text))
    return out


def _classify(text):
    """Map each numeric token to (token, start, kind)."""
    struct = _spans([r for r, _ in _STRUCT_RES], text)
    biblio = _spans(_BIBLIO_RES, text)

    def inside(pos, spans):
        return any(s <= pos < e for s, e in spans)

    out = []
    for m in NUM_RE.finditer(text):
        kind = "substantive"
        if inside(m.start(), biblio):
            kind = "bibliographic"
        elif inside(m.start(), struct):
            kind = "structural"
        out.append((m.group(0).strip(), m.start(), kind))
    return out


def _classify_tokens(text):
    """Counter-style token lists split by kind."""
    buckets = {"substantive": [], "structural": [], "bibliographic": []}
    for tok, _, kind in _classify(text):
        buckets[kind].append(tok)
    return buckets


def cmd_numbers(args):
    a = open(args.a, encoding="utf-8", errors="replace").read()
    b = open(args.b, encoding="utf-8", errors="replace").read()
    from collections import Counter
    ka, kb = _classify_tokens(a), _classify_tokens(b)
    ca, cb = Counter(ka["substantive"]), Counter(kb["substantive"])
    # "absent" and "mentioned less often" are different failures. A draft that
    # compresses four restatements of "24 h" into one has not changed a value;
    # a draft that drops a value entirely has. Only the second is drift, so only
    # the second fails the check - the count difference is still reported, in its
    # own field, so it is visible rather than suppressed.
    missing = {k: v for k, v in ca.items() if k not in cb}
    reduced = {k: [ca[k], cb[k]] for k in ca if k in cb and cb[k] < ca[k]}
    added = {k: v for k, v in cb.items() if k not in ca}
    # Structure/bibliography differences are reported for visibility but do not
    # fail the check: re-numbering sections or dropping a reference list from a
    # draft is a layout decision, not a changed value.
    sa, sb = Counter(ka["structural"] + ka["bibliographic"]), Counter(kb["structural"] + kb["bibliographic"])
    struct_missing = {k: v for k, v in sa.items() if sb.get(k, 0) < v}
    ma = sorted(set(m.group(0) for m in MARKER_RE.finditer(a)))
    mb = sorted(set(m.group(0) for m in MARKER_RE.finditer(b)))
    out = {"numbers_in_a": sum(1 for _ in NUM_RE.finditer(a)),
           "numbers_in_b": sum(1 for _ in NUM_RE.finditer(b)),
           "substantive_in_a": len(ka["substantive"]),
           "substantive_in_b": len(kb["substantive"]),
           "numbers_missing_in_b": missing, "numbers_added_in_b": added,
           "substantive_reduced_in_b": reduced,
           "structural_or_bibliographic_missing_in_b": struct_missing,
           "markers_lost": [m for m in ma if m not in mb],
           "markers_added": [m for m in mb if m not in ma],
           "units_a": sorted(set(UNIT_RE.findall(a))), "units_b": sorted(set(UNIT_RE.findall(b)))}
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


_REF_HEAD_RE = re.compile(r"(?im)^(#{1,6})\s*(references|参考文献|bibliography)\s*$")


def _split_references(text):
    """Split text into (before, reference block, after).

    Cutting at the heading and discarding the rest is fail-open: any content a
    draft puts after its bibliography is never scanned, so an unsupported claim
    placed there passes silently. The block is closed by the next heading at the
    same or a higher level instead, and whatever follows is still checked.
    """
    m = _REF_HEAD_RE.search(text)
    if not m:
        return text, "", ""
    level = len(m.group(1))
    end = len(text)
    for h in re.finditer(r"(?im)^(#{1,6})\s+\S", text[m.end():]):
        if len(h.group(1)) <= level:
            end = m.end() + h.start()
            break
    return text[:m.start()], text[m.end():end], text[end:]


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
    # Anything after the reference block is body again: dropping it would stop
    # citation markers placed below the bibliography from being audited.
    before, refs, after = _split_references(text)
    body = before + "\n" + after
    listed = {}
    for line in refs.splitlines():
        mm = re.match(r"^\s*\[(\d+)\]\s*(.+)$", line)
        if mm:
            listed[int(mm.group(1))] = mm.group(2).strip()
    cited = _cite_numbers(body)
    unlisted = sorted({n for n in cited if n not in listed})
    uncited = sorted({n for n in listed if n not in cited})
    # Headings are dropped line-wise before sentence splitting, for the same
    # reason as in cmd_claims: splitting first turns "## 1. What the material
    # reports" into a sentence that no longer starts with "#", and that fragment
    # was then counted as an uncited sentence.
    body_lines = [ln for ln in body.splitlines() if not re.match(r"^\s*#{1,6}\s", ln)]
    body = "\n".join(body_lines)
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
    # A bibliography entry is a pointer to a work, not an assertion about the
    # material, so it is not a claim and cannot be "unsupported". Dropped before
    # splitting, otherwise "Fibroblast migration in provisional matrix." and
    # "Synth Rev Metab. 2021;" were reported as unsupported claims.
    text, _, after = _split_references(text)
    text = text + "\n" + after
    # Headings are dropped line-wise, before paragraph/sentence splitting.
    # Splitting first turned "## 1. What the material reports" into the sentence
    # "What the material reports", which no longer starts with "#" and was then
    # reported as an unsupported claim.
    body_lines = [ln for ln in text.splitlines()
                  if not re.match(r"^\s*#{1,6}\s", ln)
                  and not re.match(r"^\s*\[\d+\]\s", ln)]
    text = "\n".join(body_lines)
    out = []
    for para in re.split(r"\n\s*\n", text):
        for s in SENT_SPLIT.split(para):
            s = " ".join(s.split())
            if len(re.sub(r"\W", "", s)) < 15 or s.startswith("#"):
                continue
            out.append({"claim": s[:180], "supported": bool(MARKER_RE.search(s))})
    unsupported = [c for c in out if not c["supported"]]
    print(json.dumps({"claims": len(out), "unsupported": len(unsupported),
                      "unsupported_list": unsupported[:40]}, ensure_ascii=False, indent=1))
    return 0 if not unsupported else 1


# The writing family's published contract (case_id, deliverable, preserved_facts,
# evidence_map, change_log). `status` is this skill's own addition, not a contract
# field, so it is optional here; the enum it used to check against (ok/unable/
# refused) belongs to the artifact family and never applies to a writing result.
REQUIRED = ("case_id", "deliverable", "preserved_facts", "evidence_map", "change_log")
STATUS_VALUES = ("answered", "partial", "insufficient_evidence", "input_error", None)


def cmd_result(args):
    data = json.load(open(args.path, encoding="utf-8"))
    problems = []
    for k in REQUIRED:
        if k not in data:
            problems.append(f"missing required field: {k}")
    if data.get("status") not in STATUS_VALUES:
        problems.append(f"unknown status value: {data.get('status')!r}; "
                        f"expected one of {[s for s in STATUS_VALUES if s]}")
    if data.get("status") in ("partial", "insufficient_evidence", "input_error") and not data.get("limitations"):
        problems.append("non-ok status must state its limitations")
    em = data.get("evidence_map")
    if em is not None:
        if not isinstance(em, list):
            problems.append("evidence_map must be a list")
        else:
            for i, item in enumerate(em):
                if not isinstance(item, dict):
                    problems.append(f"evidence_map[{i}] must be an object")
                    continue
                if not str(item.get("locator") or "").strip():
                    problems.append(f"evidence_map[{i}] has no locator")
    pf = data.get("preserved_facts")
    if isinstance(pf, list):
        for i, item in enumerate(pf):
            if isinstance(item, dict) and not str(item.get("source") or "").strip():
                problems.append(f"preserved_facts[{i}] has no source locator")
    blob = json.dumps(data, ensure_ascii=False)
    if re.search(r"(TODO|FIXME|PLACEHOLDER|待补)", blob):
        problems.append("placeholder text left in the deliverable")
    print(json.dumps({"file": args.path, "fields": sorted(data.keys()), "problems": problems},
                     ensure_ascii=False, indent=1))
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
