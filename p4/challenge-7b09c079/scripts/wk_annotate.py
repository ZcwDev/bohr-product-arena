#!/usr/bin/env python3
"""Deterministic, source-grounded annotation proposals + consistency review (stdlib only).

Nothing here invents text: every quoted span is a *slice* of a block that was read
from an input file, and carries the block locator plus char_start/char_end so that
selfcheck.py (or a human) can resolve it back to the original bytes.

Public functions
  sentence_candidates(blocks)            -> list of sentence records with locators
  propose_for_target(target, ...)        -> ranked annotation proposals for one target
  review_target(target, ...)             -> numbers / citations / abbreviations / term variants
"""
from __future__ import annotations

import math
import re
from collections import Counter

from wk_io import norm_cmp, norm_ws, split_sentences

STOP = set("""a an the and or of in on at to for from by with without into onto over under than then
that this these those which who whom whose what when where why how is are was were be been being
it its as such can could may might will would shall should do does did done has have had not no
also both each either neither more most less least very much many some any all other another
using used use based between within during after before about above below per via due thus hence
therefore however while whereas although though since because if so but our we they their them
there here one two three four five figure fig table eq et al ref refs results result shown show
shows study studies paper work present presented""".split())

CUE = re.compile(r"(?i)\b(limit\w*|only|however|although|whereas|unless|except|assum\w*|approximat\w*|"
                 r"depend\w*|uncertain\w*|error|deviation|variab\w*|condition\w*|caution|cannot|"
                 r"not\b|may\b|might\b|should\b|range|between|below|above|at least|up to|maximum|minimum|"
                 r"significant\w*|compared|relative|increase\w*|decrease\w*|reduc\w*|improv\w*|"
                 r"temperature|pressure|speed|rate|thickness|ratio|orientation)\b|±|[<>≤≥]|但|仅|限于|条件|可能|误差|不")

NUM = re.compile(r"(?<![\w.])[-−]?\d+(?:[.,]\d+)?(?:\s?(?:%|°\s?C|°|K\b|MPa|GPa|kPa|Pa\b|kN|N\b|J\b|kJ|"
                 r"mm|cm|µm|μm|nm|m\b|mg|kg|g\b|mL|ml|L\b|s\b|min|h\b|Hz|V\b|W\b|wt%|vol%))?")
CIT_NUM = re.compile(r"\[(\d+(?:\s*[-–,]\s*\d+)*)\]")
CIT_AY = re.compile(r"\(([A-Z][A-Za-z\-']+)(?: et al\.?)?(?:,? (?:and|&) [A-Z][A-Za-z\-']+)?,? ((?:19|20)\d{2}[a-z]?)\)")
ABBR = re.compile(r"\b[A-Z][A-Z0-9]{1,}[a-z]?s?\b")
REF_HEAD = re.compile(r"(?i)^\s*(#{1,6}\s*)?(\d+\.?\s*)?(references|bibliography|literature cited|参考文献)\s*$")


def tokens(text: str) -> list[str]:
    t = norm_cmp(text)
    out = [w for w in re.findall(r"[a-z][a-z\-]{2,}|\d+(?:\.\d+)?|[\u4e00-\u9fff]{2}", t) if w not in STOP]
    return out


def mark_reference_blocks(blocks: list[dict]) -> None:
    """Flag blocks that sit under a References heading (per file)."""
    cur_file, in_refs = None, False
    for b in blocks:
        if b["loc"]["file"] != cur_file:
            cur_file, in_refs = b["loc"]["file"], False
        first = b["text"].strip().splitlines()[0] if b["text"].strip() else ""
        if REF_HEAD.match(first) or (b.get("is_heading") and REF_HEAD.match(re.sub(r"^#+\s*", "", first))):
            in_refs = True
            b["is_ref_heading"] = True
            continue
        if in_refs and b.get("is_heading") and not REF_HEAD.match(first):
            in_refs = False
        b["is_reference_entry"] = in_refs or bool(b.get("is_reference_entry"))


def sentence_candidates(blocks: list[dict]) -> list[dict]:
    out = []
    for bi, b in enumerate(blocks):
        if b.get("is_heading") or b.get("is_reference_entry") or b.get("is_table"):
            continue
        text = b["text"]
        for s, e, seg in split_sentences(text):
            # trim leading whitespace inside the span so quote == slice
            while s < e and text[s].isspace():
                s += 1
            while e > s and text[e - 1].isspace():
                e -= 1
            seg = text[s:e]
            if len(norm_ws(seg)) < 40:
                continue
            if len(seg) > 360:  # cut at a clause boundary, keep an exact slice
                cut = max(seg.rfind(", ", 0, 320), seg.rfind("; ", 0, 320))
                if cut < 120:
                    continue
                e = s + cut
                seg = text[s:e]
            out.append({"block_index": bi, "start": s, "end": e, "text": seg,
                        "toks": tokens(seg), "role": b.get("role", "material")})
    return out


def _idf(cands: list[dict]) -> dict:
    df = Counter()
    for c in cands:
        df.update(set(c["toks"]))
    n = max(1, len(cands))
    return {w: math.log(1 + n / (1 + d)) for w, d in df.items()}


def _loc(blocks, bi, s, e) -> dict:
    loc = dict(blocks[bi]["loc"])
    loc["char_start"], loc["char_end"] = s, e
    return loc


def numbers_in(text: str) -> list[dict]:
    cit_spans = [m.span() for m in CIT_NUM.finditer(text)]
    out = []
    for m in NUM.finditer(text):
        if any(a <= m.start() < b for a, b in cit_spans):
            continue
        raw = m.group(0).strip()
        bare = re.match(r"[-−]?\d+(?:[.,]\d+)?", raw).group(0)
        if re.fullmatch(r"(19|20)\d{2}", bare) and raw == bare:
            continue  # years are bibliographic, not measured values
        out.append({"value": raw, "number": bare, "start": m.start(), "end": m.start() + len(m.group(0).rstrip())})
    return out


def find_number(blocks: list[dict], number: str, exclude: int | None, roles: set | None = None):
    pat = re.compile(r"(?<![\d.])" + re.escape(number.replace("−", "-")) + r"(?![\d])")
    for bi, b in enumerate(blocks):
        if bi == exclude or (roles and b.get("role") not in roles):
            continue
        t = b["text"].replace("−", "-")
        m = pat.search(t)
        if m:
            s = max(0, m.start() - 60)
            e = min(len(t), m.end() + 60)
            return bi, s, e
    return None


def propose_for_target(target: dict, blocks: list[dict], cands: list[dict], lang: str = "en",
                       k: int = 2) -> list[dict]:
    """Return up to k ranked annotation proposals for one target block."""
    tb = target["block_index"]
    ttext = blocks[tb]["text"]
    ttoks = Counter(tokens(ttext))
    idf = _idf(cands)
    tnums = {n["number"] for n in numbers_in(ttext)}
    scored = []
    for c in cands:
        if c["block_index"] == tb:
            continue
        if norm_cmp(c["text"]) in norm_cmp(ttext):
            continue  # restating the target itself is not an annotation
        ov = sum(idf.get(w, 0) for w in set(c["toks"]) if w in ttoks)
        if ov <= 0:
            continue
        cue = len(CUE.findall(c["text"]))
        num_overlap = len(tnums & {n["number"] for n in numbers_in(c["text"])})
        role_bonus = 1.25 if c["role"] == "source" else 1.0
        score = (ov + 0.6 * min(cue, 4) + 1.5 * num_overlap) * role_bonus
        scored.append((score, c))
    scored.sort(key=lambda x: -x[0])
    picked, seen = [], set()
    for score, c in scored:
        key = norm_cmp(c["text"])[:80]
        if key in seen:
            continue
        seen.add(key)
        picked.append((score, c))
        if len(picked) >= 2 * k + 2:
            break
    # target's own first sentence for anchoring the note
    tsent = next(((s, e) for s, e, seg in split_sentences(ttext) if len(seg.strip()) >= 20),
                 (0, min(len(ttext), 200)))
    ts, te = tsent
    while ts < te and ttext[ts].isspace():
        ts += 1
    if te - ts > 240:
        cut = ttext.rfind(" ", ts, ts + 220)
        te = cut if cut > ts + 60 else ts + 220
    proposals = []
    # build k alternative proposals: rank 1 uses best two quotes, rank 2 uses next ones
    for rank in range(k):
        group = picked[rank * 2: rank * 2 + 2]
        if not group:
            break
        quotes = [{"quote": norm_ws(c["text"]), "locator": _loc(blocks, c["block_index"], c["start"], c["end"]),
                   "score": round(s, 3), "role": c["role"]} for s, c in group]
        anchor = {"quote": norm_ws(ttext[ts:te]), "locator": _loc(blocks, tb, ts, te)}
        proposals.append({"rank": rank + 1, "anchor": anchor, "quotes": quotes,
                          "score": round(sum(q["score"] for q in quotes), 3)})
    return proposals


def review_target(target: dict, blocks: list[dict]) -> dict:
    """Numbers, citations, abbreviations and term variants of one target, with locators."""
    tb = target["block_index"]
    b = blocks[tb]
    text = b["text"]
    obs = {"numbers": [], "citations": [], "abbreviations": [], "term_variants": []}
    for n in numbers_in(text):
        hit_src = find_number(blocks, n["number"], tb, roles={"source"})
        hit_any = hit_src or find_number(blocks, n["number"], tb)
        row = {"value": n["value"], "target_locator": _loc(blocks, tb, n["start"], n["end"]),
               "found_in_source": bool(hit_src), "found_elsewhere": bool(hit_any)}
        if hit_any:
            bi, s, e = hit_any
            row["match_locator"] = _loc(blocks, bi, s, e)
            row["match_context"] = norm_ws(blocks[bi]["text"][s:e])
        obs["numbers"].append(row)
    refs = [x for x in blocks if x.get("is_reference_entry") and x["loc"]["file"] == b["loc"]["file"]]
    refs_all = [x for x in blocks if x.get("is_reference_entry")]
    for m in CIT_NUM.finditer(text):
        ids = []
        for part in re.split(r"\s*,\s*", m.group(1)):
            r = re.split(r"\s*[-–]\s*", part)
            if len(r) == 2 and r[0].isdigit() and r[1].isdigit() and int(r[1]) - int(r[0]) < 50:
                ids.extend(range(int(r[0]), int(r[1]) + 1))
            elif part.strip().isdigit():
                ids.append(int(part))
        found = {}
        for i in ids:
            pat = re.compile(r"^\s*(\[%d\]|%d\.|%d\s)" % (i, i, i))
            hit = next((x for x in (refs or refs_all) if pat.match(x["text"])), None)
            found[str(i)] = dict(hit["loc"]) if hit else None
        obs["citations"].append({"marker": m.group(0), "target_locator": _loc(blocks, tb, m.start(), m.end()),
                                 "reference_entries": found,
                                 "status": "resolved" if found and all(found.values()) else
                                 ("no_reference_list_in_inputs" if not refs_all else "unresolved")})
    for m in CIT_AY.finditer(text):
        surname = m.group(1)
        hit = next((x for x in refs_all if surname in x["text"] and m.group(2)[:4] in x["text"]), None)
        obs["citations"].append({"marker": m.group(0), "target_locator": _loc(blocks, tb, m.start(), m.end()),
                                 "reference_entries": {surname: dict(hit["loc"]) if hit else None},
                                 "status": "resolved" if hit else
                                 ("no_reference_list_in_inputs" if not refs_all else "unresolved")})
    seen = set()
    for m in ABBR.finditer(text):
        ab = m.group(0)
        if ab in seen or ab.isdigit() or len(ab) > 10:
            continue
        seen.add(ab)
        dpat = re.compile(r"\(" + re.escape(ab) + r"\)|\b" + re.escape(ab) + r"\s*\([a-z][^)]{3,}\)")
        d = None
        for bi, x in enumerate(blocks):
            mm = dpat.search(x["text"])
            if mm:
                s = max(0, mm.start() - 60)
                d = {"locator": _loc(blocks, bi, s, mm.end()), "context": norm_ws(x["text"][s:mm.end()])}
                break
        obs["abbreviations"].append({"abbr": ab, "target_locator": _loc(blocks, tb, m.start(), m.end()),
                                     "definition": d, "status": "defined" if d else "definition_not_found"})
    for m in re.finditer(r"\b([A-Za-z]{3,})-([A-Za-z]{3,})\b", text):
        a, c = m.group(1), m.group(2)
        variants = {f"{a} {c}": 0, f"{a}{c}": 0}
        where = {}
        for bi, x in enumerate(blocks):
            low = x["text"].lower()
            for v in variants:
                cnt = len(re.findall(r"\b" + re.escape(v.lower()) + r"\b", low))
                if cnt:
                    variants[v] += cnt
                    where.setdefault(v, dict(x["loc"]))
        if any(variants.values()):
            obs["term_variants"].append({"term": m.group(0), "target_locator": _loc(blocks, tb, m.start(), m.end()),
                                         "variant_counts": variants, "first_variant_locator": where})
    return obs


def compose_annotation(target_id: str, prop: dict, review: dict, lang: str) -> str:
    """Human-readable annotation text built only from verified quotes and review findings."""
    q = prop["quotes"]
    unresolved_nums = [n["value"] for n in review["numbers"] if not n["found_elsewhere"]]
    src_nums = [n["value"] for n in review["numbers"] if n["found_in_source"]]
    unresolved_cit = [c["marker"] for c in review["citations"] if c["status"] != "resolved"]
    undefined = [a["abbr"] for a in review["abbreviations"] if a["status"] != "defined"]
    if lang == "zh":
        parts = [f"【编辑注释 {target_id}｜新增编者说明，非原研究内容】"]
        parts.append(f"本段所述内容的适用条件/限定见原材料：“{q[0]['quote']}”（{q[0]['loc_str']}）。")
        if len(q) > 1:
            parts.append(f"相关表述：“{q[1]['quote']}”（{q[1]['loc_str']}）。")
        if src_nums:
            parts.append("本段数值 " + "、".join(src_nums[:6]) + " 可在来源中找到同值。")
        if unresolved_nums:
            parts.append("数值 " + "、".join(unresolved_nums[:6]) + " 未在其他材料中检索到，需作者确认。")
        if unresolved_cit:
            parts.append("引用 " + "、".join(unresolved_cit[:4]) + " 未能对应到参考文献条目，需人工核对。")
        if undefined:
            parts.append("缩写 " + "、".join(undefined[:4]) + " 未找到定义处。")
        parts.append("不确定性：本注释仅转述材料原文，不新增数据，未经作者确认。")
        return "".join(parts)
    parts = [f"[Editorial annotation {target_id} | added by editor, not part of the original research] "]
    parts.append(f"Scope/condition stated in the material: \u201c{q[0]['quote']}\u201d ({q[0]['loc_str']}).")
    if len(q) > 1:
        parts.append(f" Related statement: \u201c{q[1]['quote']}\u201d ({q[1]['loc_str']}).")
    if src_nums:
        parts.append(" Values in this paragraph found verbatim in the source: " + ", ".join(src_nums[:6]) + ".")
    if unresolved_nums:
        parts.append(" Values not found elsewhere in the supplied material (author confirmation needed): "
                     + ", ".join(unresolved_nums[:6]) + ".")
    if unresolved_cit:
        parts.append(" Citation markers not matched to a reference entry: " + ", ".join(unresolved_cit[:4]) + ".")
    if undefined:
        parts.append(" Abbreviations without a located definition: " + ", ".join(undefined[:4]) + ".")
    parts.append(" Uncertainty: this note only restates supplied text; it adds no new data and has not been confirmed by the authors.")
    return "".join(parts)
