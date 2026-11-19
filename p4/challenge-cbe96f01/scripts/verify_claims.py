#!/usr/bin/env python3
"""Claim-level evidence verification: claim -> minimal sufficient evidence -> verdict.

Contract this script implements (organizer revision v2):
  input : /input/claims-to-verify.json  [{claim_id, claim}, ...]
          /input/case.json (optional, for case_id)
          materials: PDFs / text under /input (real papers at evaluation time)
  output: /app/output/result.json with case_id, status, result.verdicts[], sources[],
          uncertainties[], limitations[], change_log[]
          plus evidence_cards.md / evidence_cards.tsv / SELFCHECK.json

Design rules that matter for scoring
  * Never assert a verdict without a verbatim quote that is re-found in the extracted
    source text (self-check fails otherwise). No quote => insufficient_evidence.
  * Decompose compound claims into atoms and judge each atom; the claim verdict is a
    function of its atoms, so "partially_supported" is principled, not a hedge.
  * Preserve comparator / negation / significance / population scope: a claim about
    humans is not supported by a mouse experiment, and "no significant change" is a
    contradiction for an affirmative efficacy claim, not missing evidence.
  * Everything degrades honestly: missing tool, empty text layer, failed locator ->
    status=partial and an explicit uncertainty, never a fabricated finding.

Usage: python3 verify_claims.py [--input /input] [--output /app/output]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from _render import maybe_pdf
except Exception:
    def maybe_pdf(*a, **k):
        return {"pdf": None, "reason": "renderer unavailable"}

VERDICTS = ("supported", "partially_supported", "contradicted", "insufficient_evidence")
STOP = set("""a an the of in on at to for from by with and or but as is are was were be been being
this that these those it its their our we they he she them than then which who whom whose what
how why when where not no nor do does did done can could may might must should would will
has have had also more most less least very such other another each any all both between
into over under about above below after before during while because due based study studies
result results data group groups case cases using used use shown show shows reported report""".split())

NEG = ("no significant", "not significant", "no difference", "did not", "didn't", "failed to",
       "no change", "not reduced", "not increase", "no effect", "without effect", "unchanged",
       "non-significant", "not support", "not observed", "no evidence of",
       # scope limiters: they negate an "all/every/always" claim even without a negation word
       "limited to", "only ", "restricted to", "cannot ", "can not ", "not all ", "except ",
       "is not ", "are not ", "does not ", "do not ")
SIG = ("significantly", "significant", "p<", "p <", "p=", "p =", "compared with", "compared to",
       "relative to", "versus", "vs.")
CMP = ("higher", "lower", "greater", "smaller", "increased", "decreased", "reduced", "elevated",
       "compared with", "compared to", "relative to", "versus", "vs.", "more than", "less than",
       "no difference", "similar")
CAUSE = ("because", "due to", "caused", "causes", "leads to", "led to", "resulted in",
         "induced", "driven by", "attributable", "mechanism")
SCOPE_HUMAN = ("human", "humans", "patients", "clinical", "in people", "volunteers")
SCOPE_ANIMAL = ("mice", "mouse", "murine", "rat", "rats", "xenograft", "rabbit", "dog", "canine",
                "in vivo model")
SCOPE_VITRO = ("in vitro", "cell line", "cells", "culture", "ex vivo", "organoid")
EFFECT_STEMS = ("reduc", "decreas", "lower", "increas", "rais", "elevat", "improv", "prolong",
                "surviv", "abolish", "eliminat", "eradicat", "inhibit", "suppress", "enhanc",
                "attenuat", "prevent", "delay", "accelerat", "restor", "shrink", "regress")
ABSOLUTE = ("abolish", "abolishes", "abolished", "eliminate", "eliminates", "eradicate",
            "eradicates", "cure", "cures", "always", "never", "all patients", "every")


# --- interpreter shim -------------------------------------------------------
# The evaluation image keeps the science stack (PyMuPDF, numpy, ...) in a separate
# venv and the python3 on PATH is a bare conda interpreter. Without this, PDF text
# extraction silently degrades to "no text layer" and every field looks unreported.
def _ensure_science_python():
    import os, sys
    try:
        import fitz  # noqa: F401
        return
    except Exception:
        pass
    for cand in (os.environ.get("WEEKLY_TOOLS_PYTHON"), "/opt/weekly-tools/bin/python",
                 "/opt/weekly-tools/bin/python3"):
        if cand and os.path.exists(cand) and os.path.realpath(cand) != os.path.realpath(sys.executable):
            try:
                os.execv(cand, [cand] + sys.argv)
            except Exception:
                continue


_ensure_science_python()


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9][a-z0-9\-']{1,}", (text or "").lower()) if w not in STOP}


def _pure_pdf_text(path) -> list[str]:
    """Standard-library PDF text reader (last resort when no PDF engine is installed).

    Handles both literal `(...)` and hex `<...>` strings (PyMuPDF writes hex strings inside TJ), turns
    positioning operators into line breaks, and skips streams that do not decompress.
    """
    import zlib
    try:
        blob = open(path, "rb").read()
    except Exception:
        return []
    chunks = []
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", blob, re.S):
        raw = m.group(1)
        try:
            raw = zlib.decompress(raw)
        except Exception:
            pass
        text = raw.decode("latin-1", "replace")
        if "BT" not in text and "Tj" not in text and "TJ" not in text:
            continue
        parts: list[str] = []
        in_array = False
        for tok in re.finditer(r"\((?:[^()\\]|\\.)*\)|<[0-9A-Fa-f\s]+>|\[|\]|[-+]?\d+(?:\.\d+)?|"
                               r"\b(?:Tj|TJ|Td|TD|T\*|ET)\b", text):
            t = tok.group(0)
            if t == "[":
                in_array = True
            elif t == "]":
                in_array = False
            elif t.startswith("("):
                parts.append(t[1:-1].replace("\\(", "(").replace("\\)", ")"))
            elif t.startswith("<"):
                hexs = re.sub(r"\s+", "", t[1:-1])
                try:
                    parts.append(bytes.fromhex(hexs if len(hexs) % 2 == 0 else hexs + "0")
                                 .decode("latin-1", "replace"))
                except Exception:
                    pass
            elif t in {"Td", "TD", "T*", "ET"}:
                parts.append("\n")
            elif in_array and re.fullmatch(r"[-+]?\d+(?:\.\d+)?", t):
                try:
                    if float(t) < -100:
                        parts.append(" ")
                except Exception:
                    pass
        chunk = "".join(parts).strip()
        if chunk:
            chunks.append(chunk)
    return chunks

def norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?。！？])\s+|\n{2,}", text or "")
    return [norm(p) for p in parts if p and len(norm(p)) > 2]


# ---------------------------------------------------------------- materials
def read_pdf(path: Path) -> tuple[list[list[str]], str]:
    """Return per-page lists of text blocks.

    `get_text("text")` almost never yields blank lines, so paragraph splitting on "\\n\\n"
    collapsed a whole page into one block and every locator degenerate to p1b1. Asking
    PyMuPDF for layout blocks keeps the real paragraph boundaries; pdftotext output is
    split on form feeds so page numbers stay correct.
    """
    try:
        import fitz
    except Exception:
        fitz = None
    if fitz is not None:
        try:
            pages = []
            for page in fitz.open(str(path)):
                blocks = [b[4] for b in page.get_text("blocks") if len(b) > 4 and norm(b[4])]
                pages.append(blocks or [page.get_text("text")])
            return pages, "pymupdf-blocks"
        except Exception as exc:  # noqa: BLE001
            why = str(exc)[:90]
    else:
        why = "pymupdf missing"
    pages = _pure_pdf_text(path)
    if pages:
        return [[c for c in re.split(r"\n\s*\n", pg) if norm(c)] for pg in pages], "stdlib-pdf"
    if subprocess.run(["which", "pdftotext"], capture_output=True).returncode == 0:
        try:
            out = subprocess.run(["pdftotext", "-layout", str(path), "-"],
                                 capture_output=True, text=True, timeout=120).stdout
            pages = [p for p in out.split("\f")]
            return [[c for c in re.split(r"\n\s*\n", p) if norm(c)] for p in pages if p.strip()], "pdftotext"
        except Exception:  # noqa: BLE001
            pass
    return [], f"no text layer readable ({why}); scan/OCR would be required"


SKIP_NAMES = {"result.json", "evidence_cards.md", "evidence_cards.tsv",
              "field_cards.md", "fields.tsv", "selfcheck.json", "run.md", "skill.md",
              "gain.md", "rubric.md", "formula_cards.md"}
REFERENCE_HEAD = re.compile(r"^\s*(references|bibliography|works cited|参考文献)\s*$", re.I)


def load_materials(root: Path) -> tuple[list[dict], list[str]]:
    blocks: list[dict] = []
    notes: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".pdf", ".txt", ".md"}:
            continue
        low = path.name.lower()
        if low.startswith(("claims-to-verify", "target-fields", "case.json", "task.md")):
            continue
        if low in SKIP_NAMES or low.endswith(("-arm.zip", ".py", ".png", ".svg", ".jsonl")):
            continue
        if path.suffix.lower() == ".pdf":
            pages, how = read_pdf(path)
            if not pages:
                notes.append(f"{path.name}: {how}")
                continue
            notes.append(f"{path.name}: text via {how}, {len(pages)} page(s)")
        else:
            text = path.read_text(encoding="utf-8", errors="replace")
            pages = [[c for c in re.split(r"\n\s*\n", p) if norm(c)] for p in re.split(r"\f", text) if p.strip()]
            notes.append(f"{path.name}: plain text")
        for pno, page_blocks in enumerate(pages, 1):
            in_references = False
            for bidx, chunk in enumerate(page_blocks, 1):
                first_line = norm(chunk.splitlines()[0]) if chunk.strip() else ""
                if REFERENCE_HEAD.match(first_line) or REFERENCE_HEAD.match(norm(chunk)[:40]):
                    in_references = True           # a citation list is not evidence
                if in_references:
                    continue
                text = norm(chunk)
                blocks.append({"source": path.name, "page": pno, "block": bidx,
                               "locator": f"p{pno}b{bidx}", "text": text,
                               "sentences": split_sentences(text)})
    return blocks, notes


# ---------------------------------------------------------------- claims
def decompose(claim: str) -> list[dict]:
    """Split a compound claim into atoms, carrying the subject to the later atoms.

    Two things that matter here:
      * "Compound A reduced tumour volume and decreased body weight" - the second atom has
        no subject; without carrying it the retrieval loses its object and matches any
        sentence about body weight.
      * "between X and Y" / "A and B combination" are noun phrases, not two claims.
    """
    text = norm(claim)
    protected = re.sub(r"\band\b", "&", text) if re.search(r"(between|combination of)\b[^.;]{0,40}\band\b", text) else text
    raw = re.split(r";\s+|\s+&\s+|\s+and\s+|,\s+(?=[a-z]+ed\b|[a-z]+s\b)", protected)
    atoms: list[dict] = []
    subject = ""
    for piece in raw:
        piece = norm(piece.replace("&", "and")).strip(",; ")
        if len(piece.split()) < 2:
            continue
        low = piece.lower()
        kind = "fact"
        if any(t in low for t in CAUSE):
            kind = "causal"
        elif any(t in low for t in CMP):
            kind = "comparison"
        if any(t in low for t in SCOPE_HUMAN + SCOPE_ANIMAL + SCOPE_VITRO + ABSOLUTE):
            kind = "scope" if kind == "fact" else kind + "+scope"
        if not subject:
            head = re.split(r"\b(?:is|are|was|were|has|have|had|shows?|showed|reduces?|reduced|"
                            r"decreases?|decreased|increases?|increased|abolishes?|abolished|"
                            r"improves?|improved|leads?|led)\b", piece, maxsplit=1)[0]
            subject = norm(head)[:80]
        atom_text = piece if subject in piece or not subject else f"{subject} {piece}"
        atoms.append({"atom": atom_text, "type": kind, "carried_context": subject})
    if not atoms:
        atoms = [{"atom": text, "type": "fact", "carried_context": ""}]
    return atoms


def score_block(atom: str, block: dict) -> float:
    aw = words(atom)
    bw = words(block["text"])
    if not aw:
        return 0.0
    overlap = len(aw & bw)
    score = overlap / max(1, len(aw))
    if any(tok in block["text"].lower() for tok in ("significant", "p<", "p =", "p=")):
        score += 0.15
    for number in re.findall(r"\d+(?:\.\d+)?", atom):
        if number in block["text"]:
            score += 0.1
    low = atom.lower()
    if any(t in low for t in SCOPE_HUMAN) and any(t in block["text"].lower() for t in SCOPE_HUMAN):
        score += 0.1
    return score


def best_evidence(atom: str, blocks: list[dict], k: int = 3) -> list[dict]:
    scored = sorted(((score_block(atom, b), b) for b in blocks), key=lambda x: -x[0])
    picked = [b for s, b in scored[:k] if s >= 0.25]
    return picked


def judge(atom: str, blocks: list[dict]) -> dict:
    """Rule-based entailment for one atom, returning verdict + supporting/negating quotes."""
    ev = best_evidence(atom, blocks)
    if not ev:
        return {"verdict": "insufficient_evidence", "evidence": [],
                "why": "检索不到与断言共享实词的段落（未报告或材料不含该结论）"}
    low_atom = atom.lower()
    supports, negates = [], []
    for block in ev:
        atom_stems = {st for st in EFFECT_STEMS if st in low_atom}
        for sidx, sent in enumerate(block.get("sentences") or split_sentences(block["text"]), 1):
            sw, aw = words(sent), words(atom)
            if not aw or len(aw & sw) < max(2, int(0.34 * len(aw))):
                continue
            # A sentence only bears on this atom if it talks about the SAME effect.
            # Without this gate "did not abolish tumour growth" is mis-read as a
            # negation of "reduced tumour volume" (both mention compound/tumour/volume).
            low_sent = sent.lower()
            same_effect = any(st in low_sent for st in atom_stems) if atom_stems else True
            if not same_effect:
                # One exception: a generic null-result sentence ("no significant change in X")
                # does negate a directional claim about X, even though it repeats no verb.
                generic_null = (any(cue in low_sent for cue in ("no significant", "not significant",
                                                                "no change", "unchanged", "no difference"))
                                and len(aw & sw) >= 0.5 * len(aw))
                if not generic_null:
                    continue
            rec = {"locator": f'{block["locator"]}s{sidx}', "source": block["source"],
                   "page": block["page"], "quote": sent[:400]}
            if any(cue in low_sent for cue in NEG) or (not same_effect and len(aw & sw) >= 0.5 * len(aw)):
                negates.append(rec)
            elif any(cue in sent.lower() for cue in SIG + CMP) or len(aw & sw) >= 3:
                supports.append(rec)
    scope_conflict = ""
    if any(t in low_atom for t in SCOPE_HUMAN) and ev:
        joined = " ".join(b["text"].lower() for b in ev)
        if any(t in joined for t in SCOPE_ANIMAL + SCOPE_VITRO) and not any(t in joined for t in SCOPE_HUMAN):
            scope_conflict = "断言说人类/临床，证据是动物或体外模型（人群范围不匹配）"
    if scope_conflict:
        return {"verdict": "insufficient_evidence", "evidence": supports[:2],
                "why": scope_conflict + "；不能把模型实验外推成人群结论"}
    if any(t in low_atom for t in ABSOLUTE) and supports and not negates:
        head = supports[0]["quote"].lower()
        if any(q in head for q in ("partially", "reduced but", "did not abolish", "incomplete")):
            return {"verdict": "partially_supported", "evidence": supports[:2],
                    "why": "证据只显示部分效应，不支撑断言里的绝对化表述"}
    if supports and negates:
        return {"verdict": "partially_supported", "evidence": (supports + negates)[:3],
                "why": "同一原子既有支持句也有否定/不显著句，需并列双方并给出限定判定"}
    if negates:
        return {"verdict": "contradicted", "evidence": negates[:2],
                "why": "证据句明确给出不显著/无差异/未达到，与断言方向相反"}
    if supports:
        return {"verdict": "supported", "evidence": supports[:2],
                "why": "证据句在相同实验条件下给出与断言一致的方向与显著性"}
    return {"verdict": "insufficient_evidence", "evidence": [],
            "why": "只有主题相关的段落，缺少可判定方向的证据句（主题相关不等于支持）"}


def combine(atoms: list[dict]) -> tuple[str, str]:
    verdicts = [a["verdict"] for a in atoms]
    if all(v == "supported" for v in verdicts):
        return "supported", "全部子判断都被证据支持"
    if all(v == "contradicted" for v in verdicts):
        return "contradicted", "全部子判断与证据相反"
    if all(v == "insufficient_evidence" for v in verdicts):
        return "insufficient_evidence", "没有任何子判断获得可判定方向的证据"
    if "contradicted" in verdicts or "insufficient_evidence" in verdicts:
        return "partially_supported", "部分子判断成立、部分相反或证据不足，整体不能判为支持"
    return "partially_supported", "部分子判断成立，其余仅有部分证据"



def _claims_from_inputs(inp: Path) -> list[dict]:
    """Claims when the case does not ship claims-to-verify.json.

    Period-4 cases vary: some put the claims in case.json (claims / claims_to_verify /
    claims_to_check), some give a plain .txt/.md list. All of them are read here so the audit
    runs on the supplied claims instead of failing with input_error.
    """
    out: list[dict] = []
    case = {}
    if (inp / "case.json").is_file():
        try:
            case = json.loads((inp / "case.json").read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            case = {}
    for key in ("claims", "claims_to_verify", "claims_to_check", "items"):
        val = case.get(key) if isinstance(case, dict) else None
        if isinstance(val, list):
            for i, item in enumerate(val, 1):
                if isinstance(item, dict) and item.get("claim"):
                    out.append({"claim_id": str(item.get("claim_id") or f"C{i}"),
                                "claim": str(item["claim"])})
                elif isinstance(item, str) and len(item) > 15:
                    out.append({"claim_id": f"C{i}", "claim": item})
    if out:
        return out
    for path in sorted(inp.glob("*claim*")):
        if path.suffix.lower() in {".txt", ".md"} and path.is_file():
            lines = [re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", l).strip()
                     for l in path.read_text(encoding="utf-8", errors="replace").splitlines()]
            for i, line in enumerate([x for x in lines if len(x) > 15], 1):
                out.append({"claim_id": f"C{i}", "claim": line})
            if out:
                return out
    return out


def _write_claims_stub(out: Path, claims: list[dict]) -> Path:
    path = out / "claims-from-input.json"
    path.write_text(json.dumps(claims, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    args = ap.parse_args()
    inp, out = Path(args.input), Path(args.output)
    out.mkdir(parents=True, exist_ok=True)

    case_id = "unreported"
    case_file = inp / "case.json"
    if case_file.is_file():
        try:
            case_id = str(json.loads(case_file.read_text(encoding="utf-8")).get("case_id") or "unreported")
        except Exception:  # noqa: BLE001
            uncertainties.append("case.json 无法解析，case_id 记 unreported")
    claims_path = None
    for cand in (inp / "claims-to-verify.json", *inp.glob("*claim*.json")):
        if cand.is_file():
            claims_path = cand
            break
    if claims_path is None:
        # this challenge may hand the claims inside case.json or as a plain text list
        claims = _claims_from_inputs(inp)
        if claims:
            claims_path = _write_claims_stub(out, claims)
    uncertainties: list[str] = []
    limitations = ["判定基于 PDF 文本层与规则式蕴含判断；图表/补充材料里未进入文本层的证据不会被检索到",
                   "四级判定只区分方向性与显著性线索，不做统计学复算（样本量、效应量需人工复核）"]
    if claims_path is None:
        (out / "result.json").write_text(json.dumps({
            "case_id": case_id, "status": "input_error",
            "result": {"verdicts": []},
            "uncertainties": ["找不到 /input/claims-to-verify.json"],
            "limitations": limitations, "sources": [], "change_log": ["v1: 输入缺失，未作判定"]},
            ensure_ascii=False, indent=1), encoding="utf-8")
        # still leave a readable artefact so the bundle is complete and the reason is visible
        cards = out / "evidence_cards.md"
        try:
            cards.write_text("# 证据卡（无可用论断输入）\n\n"
                             "输入里没有 claims/论断清单（需要 claims.json / claims.csv 或正文中含可核验论断）。\n"
                             "本文件如实记录未产出证据卡的原因，不做任何推断。\n", encoding="utf-8")
            payload.setdefault("artifacts", {})["evidence_cards"] = "evidence_cards.md"
            payload["pdf"] = maybe_pdf(cards, out)
        except Exception:
            pass
        print("no claims file; wrote input_error result")
        return 0
    claims = json.loads(claims_path.read_text(encoding="utf-8"))
    case_id = "unreported"
    case_file = inp / "case.json"
    if case_file.is_file():
        try:
            case_id = str(json.loads(case_file.read_text(encoding="utf-8")).get("case_id") or "unreported")
        except Exception:  # noqa: BLE001
            uncertainties.append("case.json 无法解析，case_id 记为 unreported")
    blocks, notes = load_materials(inp)
    if not blocks:
        uncertainties.append("材料里没有任何可读文本，全部断言只能判 insufficient_evidence")
    verdicts = []
    for idx, item in enumerate(claims, 1):
        # a claims file may be a list of strings, of {"claim": ...} or of {"text": ...}: accept all three
        if isinstance(item, str):
            claim, claim_id = item.strip(), f"C{idx}"
        elif isinstance(item, dict):
            claim = str(item.get("claim") or item.get("text") or "").strip()
            claim_id = item.get("claim_id") or item.get("id") or f"C{idx}"
        else:
            claim, claim_id = str(item).strip(), f"C{idx}"
        if not claim:
            continue
        atoms = decompose(claim)
        judged = []
        for atom in atoms:
            res = judge(atom["atom"], blocks)
            judged.append({**atom, **res})
        verdict, why = combine(judged)
        verdicts.append({
            "claim_id": claim_id,
            "claim": claim,
            "verdict": verdict,
            "reason": why,
            "atoms": [{"atom": a["atom"], "type": a["type"], "verdict": a["verdict"],
                       "why": a["why"],
                       "evidence": [{"locator": e["locator"], "source": e["source"],
                                     "quote": e["quote"], "exists_in_source": True}
                                    for e in a["evidence"]]} for a in judged],
        })
    result = {"case_id": case_id,
              "status": "answered" if blocks else "partial",
              "result": {"verdicts": verdicts},
              "sources": [{"name": n} for n in notes],
              "uncertainties": uncertainties,
              "limitations": limitations,
              "change_log": ["v1: 规则式断言拆解 + 最小充分证据检索 + 四级判定；引文全部回原文校验"]}
    result["pdf"] = maybe_pdf(out / "evidence_cards.md", out)
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    rows = ["claim_id\tatom\tverdict\tlocator\tquote"]
    for v in verdicts:
        for a in v["atoms"]:
            quote = (a["evidence"][0]["quote"] if a["evidence"] else "").replace("\t", " ")
            loc = a["evidence"][0]["locator"] if a["evidence"] else "-"
            rows.append(f'{v["claim_id"]}\t{a["atom"][:80]}\t{a["verdict"]}\t{loc}\t{quote[:160]}')
    (out / "evidence_cards.tsv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    cards = ["# Evidence cards", ""]
    for v in verdicts:
        cards.append(f'## {v["claim_id"]}: {v["verdict"]}')
        cards.append(f'claim: {v["claim"]}')
        cards.append(f'reason: {v["reason"]}')
        for a in v["atoms"]:
            cards.append(f'- atom ({a["type"]}): {a["atom"]} -> **{a["verdict"]}** ({a["why"]})')
            for e in a["evidence"]:
                cards.append(f'  - {e["locator"]} [{e["source"]}]: "{e["quote"][:220]}"')
        cards.append("")
    (out / "evidence_cards.md").write_text("\n".join(cards), encoding="utf-8")

    # self-check: every quote must be re-found verbatim in the extracted material
    joined = " ".join(b["text"] for b in blocks)
    bad = []
    for v in verdicts:
        for a in v["atoms"]:
            for e in a["evidence"]:
                if e["quote"][:80] and e["quote"][:80] not in joined:
                    bad.append(f'{v["claim_id"]}/{e["locator"]}')
    check = {"quotes_rechecked": sum(len(a["evidence"]) for v in verdicts for a in v["atoms"]),
             "quotes_not_found": bad,
             "verdict_enums_valid": all(v["verdict"] in VERDICTS for v in verdicts),
             "claims_covered": len(verdicts) == len(claims)}
    _pdf = maybe_pdf(out / "evidence_cards.md", out)
    (out / "SELFCHECK.json").write_text(json.dumps(check, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"claims": len(verdicts), "blocks": len(blocks),
                      "quotes_not_found": len(bad), "status": result["status"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
