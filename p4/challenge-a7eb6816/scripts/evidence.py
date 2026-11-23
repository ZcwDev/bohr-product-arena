#!/usr/bin/env python3
"""Evidence binding for reading tasks: locate, quote-verify, split, verdict.

Everything a reading deliverable claims must be traceable to a locator in the
material actually read. This module supplies the mechanical half of that:

  * `LocatorIndex` - search extracted blocks (from pdf_text.py) and return the
    exact `p<page>b<block>` locator plus the surrounding sentence;
  * `verify_quote` - is a quoted string really present, and where (whitespace,
    soft hyphen and ligature tolerant, but never fuzzy beyond that);
  * `split_compound` - break a compound claim into atomic claims so that a
    partially true statement cannot ride on one "supported" verdict;
  * `number_spans` - every number with its unit, and whether an uncertainty
    (+- / CI / p-value) or a condition precedes it;
  * `verdict_of` - a conservative verdict scaffold driven by the checks, which
    the agent must still justify in words.

CLI:
  python3 evidence.py locate  blocks.json "some quote" [--limit 5]
  python3 evidence.py verify  blocks.json quotes.json      # [{"id":..,"quote":..}]
  python3 evidence.py split   claims.txt -o claims.json
  python3 evidence.py numbers blocks.json --filter "report"
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata

LIGATURES = {"\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi",
             "\ufb04": "ffl", "\u2010": "-", "\u2011": "-", "\u2012": "-",
             "\u2013": "-", "\u2014": "-", "\u2212": "-", "\u00a0": " ",
             "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"'}

SENT_SPLIT = re.compile(r"(?<=[.!?;])\s+(?=[A-Z\u4e00-\u9fff])")
CLAUSE_SPLIT = re.compile(r"\s*(?:;|\u3001|\uff0c\s*(?=and|but|while|whereas)|\bbut\b|\bwhereas\b|"
                          r"\bhowever\b|,\s*(?=and\s+(?:also\s+)?(?:the|a|an|no|not)\b))\s*", re.I)

NUMBER_RE = re.compile(
    r"(?<![\w.])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][-+]?\d+)?"
    r"(?:\s*(?:%|\u2030))?"
    r"(?:\s*(?:[a-zA-Z\u00b5\u03bc]{1,6}(?:/[a-zA-Z]{1,6})?(?:\^?\d)?|\u00b0C|\u00b0))?")

UNCERTAINTY_RE = re.compile(r"(?:\u00b1|±|\+/-|confidence interval|\bCI\b|\bp\s*[<=>]|\bp\s*=\s*0?\.\d|"
                            r"\bS\.?D\.?\b|\bS\.?E\.?\b|\bsem\b|\berror\b|\u6807\u51c6\u5dee)", re.I)
NEGATION_RE = re.compile(r"\b(?:no|not|never|neither|nor|without|lack(?:s|ed)?|fail(?:s|ed)? to|"
                         r"un(?:changed|affected|altered)|insignificant|non-?significant|"
                         r"\u672a|\u4e0d|\u65e0|\u6ca1\u6709)\b", re.I)
COMPARATOR_RE = re.compile(r"\b(?:higher|lower|greater|less|smaller|larger|increased|decreased|"
                           r"reduced|elevated|enhanced|attenuated|up-?regulat\w+|down-?regulat\w+|"
                           r"more|fewer|than|vs\.?|versus|compared (?:to|with)|relative to|"
                           r"\u9ad8\u4e8e|\u4f4e\u4e8e|\u5927\u4e8e|\u5c0f\u4e8e|\u589e\u52a0|\u51cf\u5c11)\b", re.I)
MODALITY_RE = re.compile(r"\b(?:may|might|could|suggests?|suggesting|indicates?|appears? to|"
                         r"is likely|possibly|potentially|tends? to|propose[ds]?|"
                         r"\u53ef\u80fd|\u6216\u8bb8|\u63d0\u793a|\u503e\u5411\u4e8e)\b", re.I)
SCOPE_RE = re.compile(r"\b(?:in vitro|in vivo|ex vivo|in silico|mouse|mice|rat|human|patients?|"
                      r"cell lines?|HEK|HeLa|zebrafish|yeast|E\. coli|n\s*=\s*\d+|"
                      r"\u4f53\u5916|\u4f53\u5185|\u5c0f\u9f20|\u4eba\u4f53)\b", re.I)


def normalize(text: str) -> str:
    out = []
    for ch in text:
        out.append(LIGATURES.get(ch, ch))
    text = "".join(out)
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)      # de-hyphenate line breaks
    text = re.sub(r"\s+", " ", text)
    return text.strip()


class LocatorIndex:
    """Search index over blocks carrying a page/block locator."""

    def __init__(self, payload: dict | list):
        blocks = payload.get("blocks") if isinstance(payload, dict) else payload
        self.blocks = [b for b in (blocks or []) if (b.get("text") or "").strip()]
        self._norm = [normalize(b.get("text", "")) for b in self.blocks]
        self._lower = [t.lower() for t in self._norm]
        self._joined = normalize(" ".join(self._norm))
        self._offsets: list[tuple[int, int, int]] = []
        cursor = 0
        for i, t in enumerate(self._norm):
            self._offsets.append((cursor, cursor + len(t), i))
            cursor += len(t) + 1

    def _locator(self, block: dict) -> str:
        loc = block.get("locator")
        if loc:
            return loc
        page = block.get("page")
        idx = block.get("id") or block.get("block_id") or block.get("index")
        if page is not None and idx is not None:
            return f"p{page}b{idx}"
        return f"block{block.get('index', '?')}"

    def locate(self, quote: str, limit: int = 5) -> list[dict]:
        needle = normalize(quote).lower()
        if not needle:
            return []
        hits = []
        for i, text in enumerate(self._lower):
            pos = text.find(needle)
            if pos >= 0:
                hits.append(self._hit(i, pos, pos + len(needle)))
                if len(hits) >= limit:
                    return hits
        if not hits and len(needle) >= 24:
            for i, text in enumerate(self._lower):
                for cut in (24, 40, 60):
                    if len(needle) > cut and needle[:cut] in text:
                        pos = text.find(needle[:cut])
                        hits.append(self._hit(i, pos, pos + cut, partial=True))
                        break
                if len(hits) >= limit:
                    break
        return hits

    def _hit(self, i: int, start: int, end: int, partial: bool = False) -> dict:
        block = self.blocks[i]
        text = self._norm[i]
        left = max(0, start - 140)
        right = min(len(text), end + 140)
        return {
            "locator": self._locator(block),
            "page": block.get("page"),
            "type": block.get("type") or block.get("kind"),
            "match": text[start:end],
            "context": ("…" if left else "") + text[left:right] + ("…" if right < len(text) else ""),
            "exact": not partial,
        }

    def sentence_around(self, quote: str) -> str | None:
        hits = self.locate(quote, limit=1)
        if not hits:
            return None
        context = hits[0]["context"]
        return context.strip("…")

    def numbers(self, needle: str | None = None, limit: int = 200) -> list[dict]:
        out = []
        for i, text in enumerate(self._norm):
            if needle and needle.lower() not in text.lower():
                continue
            for m in NUMBER_RE.finditer(text):
                start = max(0, m.start() - 90)
                window = text[start:m.end() + 90]
                out.append({
                    "locator": self._locator(self.blocks[i]),
                    "value": m.group(0).strip(),
                    "sentence": window.strip(),
                    "uncertainty": bool(UNCERTAINTY_RE.search(window)),
                    "condition": bool(SCOPE_RE.search(window)),
                })
                if len(out) >= limit:
                    return out
        return out


def verify_quote(index: LocatorIndex, quote: str) -> dict:
    hits = index.locate(quote)
    if not hits:
        return {"quote": quote, "found": False, "exact": False, "locator": None,
                "note": "not found in the material actually read; do not attribute it"}
    best = hits[0]
    exact = best["exact"] and normalize(quote).lower() == best["match"].lower()
    return {"quote": quote, "found": True, "exact": exact, "locator": best["locator"],
            "context": best["context"], "alternatives": [h["locator"] for h in hits[1:]]}


def split_compound(claim: str) -> list[dict]:
    """Split a compound claim into atomic claims, keeping the shared frame."""
    claim = claim.strip()
    if not claim:
        return []
    clauses = [c.strip(" .,;") for c in CLAUSE_SPLIT.split(claim) if c.strip(" .,;")]
    atoms = []
    for clause in clauses:
        parts = [p.strip(" .,;") for p in SENT_SPLIT.split(clause) if p.strip(" .,;")]
        atoms.extend(parts or [clause])
    out = []
    for atom in atoms:
        out.append({
            "claim": atom,
            "has_negation": bool(NEGATION_RE.search(atom)),
            "has_comparator": bool(COMPARATOR_RE.search(atom)),
            "is_modal": bool(MODALITY_RE.search(atom)),
            "has_scope": bool(SCOPE_RE.search(atom)),
            "numbers": [m.group(0).strip() for m in NUMBER_RE.finditer(atom)],
        })
    return out


def verdict_of(evidence: list[dict], supports: bool | None = None,
               contradicts: bool = False, scope_matches: bool = True) -> dict:
    """Conservative verdict scaffold. The agent must still justify it in words."""
    located = [e for e in evidence if e.get("found")]
    if contradicts and located:
        verdict, why = "contradicted", "a located source states the opposite"
    elif not located:
        verdict, why = "insufficient_evidence", "no located source in the supplied material"
    elif supports is None:
        verdict, why = "insufficient_evidence", "quote located but entailment not yet argued"
    elif supports and scope_matches and all(e.get("exact", True) for e in located):
        verdict, why = "supported", "every atomic part located verbatim and in scope"
    elif supports:
        verdict, why = "partially_supported", "only part of the claim is covered, or scope/condition differs"
    else:
        verdict, why = "insufficient_evidence", "located text does not entail the claim"
    return {"verdict": verdict, "reason": why,
            "evidence": [{"locator": e.get("locator"), "quote": e.get("quote"),
                          "exact": e.get("exact")} for e in evidence],
            "scope_matches": scope_matches}


def main() -> int:
    ap = argparse.ArgumentParser(description="Evidence binding helpers")
    sub = ap.add_subparsers(dest="cmd", required=True)
    lo = sub.add_parser("locate"); lo.add_argument("blocks"); lo.add_argument("quote")
    lo.add_argument("--limit", type=int, default=5)
    ve = sub.add_parser("verify"); ve.add_argument("blocks"); ve.add_argument("quotes")
    sp = sub.add_parser("split"); sp.add_argument("claims"); sp.add_argument("-o", "--out")
    nu = sub.add_parser("numbers"); nu.add_argument("blocks"); nu.add_argument("--filter")
    a = ap.parse_args()

    if a.cmd == "split":
        text = open(a.claims, encoding="utf-8").read()
        lines = [l.strip() for l in text.splitlines() if l.strip() and not l.startswith("#")]
        result = [{"source_claim": l, "atoms": split_compound(l)} for l in lines]
        payload = json.dumps(result, ensure_ascii=False, indent=1)
        if a.out:
            open(a.out, "w", encoding="utf-8").write(payload)
            print(a.out)
        else:
            print(payload)
        return 0

    index = LocatorIndex(json.load(open(a.blocks, encoding="utf-8")))
    if a.cmd == "locate":
        print(json.dumps(index.locate(a.quote, a.limit), ensure_ascii=False, indent=1))
    elif a.cmd == "verify":
        quotes = json.load(open(a.quotes, encoding="utf-8"))
        if isinstance(quotes, dict):
            quotes = [{"id": k, "quote": v} for k, v in quotes.items()]
        out = []
        for item in quotes:
            res = verify_quote(index, item.get("quote", ""))
            res["id"] = item.get("id")
            out.append(res)
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        print(json.dumps(index.numbers(a.filter), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
