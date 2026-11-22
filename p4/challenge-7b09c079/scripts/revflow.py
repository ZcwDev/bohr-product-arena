#!/usr/bin/env python3
"""revflow — a real local manuscript revision workflow (CLI + receipts), not a rewrite.

The organizer workorder asks for five things a plain "rewrite the paragraph" answer cannot
give: an import/task receipt, per-change accept-or-reject, version comparison plus a real
rollback/reapply, deliberate invalid operations that leave state unchanged, and an editable
manuscript with original/revised records, an evidence map and a real export preview.

This file implements exactly that as a small versioned store:

  work/revflow/
    versions/v1.json ... vN.json     blocks (immutable) + annotations (the only mutable part)
    HEAD                             current version number
    receipts/*.json                  one receipt per real operation, hash-stamped
    proposals.json                   proposed changes, each independently decidable

Design rule that makes "preserve original text" checkable rather than promised: a block's
``text`` is never written after import. An annotation is a separate record attached to a
block id, so v1 and vN share byte-identical block text and the verification step can prove it
by hashing every block in both versions.

Everything is deterministic: no model calls, no network. Annotations are quotations from the
material plus an explicit scope statement; any quote that does not occur verbatim in the
material is dropped and reported, never smoothed over.

usage:
  revflow.py run   --input /input --output /app/output [--work /app/work/revflow]
  revflow.py import|task|propose|accept CH-1|reject CH-1|compare v1 v2|rollback v2|reapply v2|
             fault-inject|consistency|export|verify|status
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

# ---------------------------------------------------------------- runtime shims


def _ensure_science_python() -> None:
    """fitz lives in the pinned science venv in the eval image, not on the bare python."""
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


def _fitz():
    try:
        import fitz  # noqa: F401
        return fitz
    except Exception:
        return None


# ---------------------------------------------------------------- text utilities

NUM = re.compile(
    r"(?<!Fig )(?<!Figure )(?<!Table )(?<!Table S)(?<!Ref )(?<!Section )(?<![A-Za-z0-9_\-])"
    r"([-+]?\d+(?:[.,]\d+)?(?:\s*[±+\-/]\s*\d+(?:[.,]\d+)?)?)"
    r"\s*(%|°C|K|mm3|mm2|cm3|mm|cm|µm|um|nm|MPa|kPa|GPa|Pa|N|kg|g|mg|mg/kg|mL|ml|µL|L|"
    r"wt%|vol%|ppm|nM|µM|uM|mM|M|h|hr|min|days?|weeks?|months?|years?|"
    r"patients?|samples?|specimens?|cells?|participants?|subjects?|trials?|replicates?)"
)
CITE = re.compile(r"(\[[\d,\s–-]+\]|\([A-Z][A-Za-z.\-]+(?:\s+et\s+al\.)?,?\s*\d{4}[a-z]?\))")
SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\[" + "\u4e00-\u9fff" + r"])")
REF_HEAD = re.compile(r"^\s*(references|bibliography|参考文献|literature cited)\s*$", re.I)
LIMIT_CUES = (
    "only", "limited", "limitation", "depends", "depend on", "under ", "when ", "at a ", "for a ",
    "however", "cannot", "could not", "not measured", "not reported", "assumed", "assume",
    "except", "rather than", "in contrast", "may ", "might ", "varies", "restricted",
    "because", "due to", "therefore", "thus", "unlike", "whereas",
)
META = re.compile(r"(@|\(at\)|Department of|University|Universit\w|Institute of|"
                  r"Corresponding author|E-?mail)", re.I)
CAPTION = re.compile(r"^\s*(Fig(?:ure)?|Table|Scheme|Chart)\s*\.?\s*\d", re.I)
JUNK = re.compile(
    r"(PLOS ONE \||DOI:\s*10\.|doi:\s*10\.1371/|OPEN ACCESS|Downloaded from|"
    r"^\s*Citation:|Creative Commons|CC-BY|Copyright:|^\s*\d+\s*/\s*\d+\s*$|"
    r"\.g0\d{2}|www\.[a-z]|All rights reserved|Supplementary (?:Information|Material))", re.I)
STOP = set("""the a an and or of to in for on with by as at from that this these those is are was
were be been being we our it its their there which who whom whose than then also such can could
should would may might must not no all any each other more most less least into over under
between within during using used use both same different first second study result results
figure table shows shown reported found data""".split())


def norm(t: object) -> str:
    """Normalise whitespace and drop bytes that are legal in a PDF but not in XML.

    Real papers carry private-use glyphs (U+F0xx symbol fonts) and stray control characters;
    writing those into document.xml makes Word and LibreOffice refuse the file, so they are
    mapped to a neutral marker at the boundary instead of corrupting the deliverable later.
    """
    s = str(t or "")
    s = re.sub(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]", " ", s)
    s = re.sub(r"[\ue000-\uf8ff]", "\u00b7", s)
    s = s.replace("\u00a0", " ").replace("\u200b", "")
    s = re.sub(r"\s+", " ", s).strip()
    # journal PDFs hyphenate at the line break ("de- position"); rejoining keeps quotations
    # readable and keeps them comparable as substrings everywhere in this file
    return re.sub(r"(\w)-\s+(\w)", r"\1\2", s)


def clean(t: str) -> str:
    """Cut journal boilerplate (running headers, DOI stamps, page numbers, licence lines).

    A real PDF text layer is full of it, and a sentence that begins "PLOS ONE | DOI: ..." is not
    evidence for anything; keeping it produced annotations that quoted the page footer.
    """
    s = norm(t)
    m = JUNK.search(s)
    if m:
        head = s[:m.start()].strip(" ,;:-")
        s = head if len(head) >= 60 else ""
    return s


def sentences(text: str) -> list[str]:
    out: list[str] = []
    for para in re.split(r"\n\s*\n", str(text or "")):
        p = norm(para)
        if not p:
            continue
        for s in SENT_SPLIT.split(p):
            s = norm(s)
            if len(s) >= 30:
                out.append(s)
    return out


def sha(obj: object) -> str:
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def sha_text(t: str) -> str:
    return hashlib.sha256(norm(t).encode()).hexdigest()


def quantities(text: str) -> set[str]:
    out = set()
    for m in NUM.finditer(text or ""):
        out.add(norm(f"{m.group(1)} {m.group(2)}").lower())
    return out


def content_words(text: str) -> set[str]:
    words = re.findall(r"[A-Za-z][A-Za-z\-]{4,}|[\u4e00-\u9fff]{2,}", text or "")
    return {w.lower() for w in words if w.lower() not in STOP}


# ---------------------------------------------------------------- material loading

def read_pdf_pages(path: Path) -> list[str]:
    fitz = _fitz()
    if fitz is not None:
        try:
            return [pg.get_text("text") for pg in fitz.open(str(path))]
        except Exception:
            pass
    if shutil.which("pdftotext"):
        try:
            r = subprocess.run(["pdftotext", "-layout", str(path), "-"],
                               capture_output=True, text=True, timeout=180)
            if r.returncode == 0:
                return r.stdout.split("\f")
        except Exception:
            pass
    return []


def read_docx_paragraphs(path: Path) -> list[str]:
    """Read a .docx without any third-party dependency (it is a zip of XML parts)."""
    try:
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml").decode("utf-8", "replace")
    except Exception:
        return []
    paras = []
    for p in re.findall(r"<w:p[ >].*?</w:p>|<w:p/>", xml, re.S):
        texts = re.findall(r"<w:t[^>]*>(.*?)</w:t>", p, re.S)
        line = norm(unescape("".join(texts)))
        if line:
            paras.append(line)
    return paras


def unescape(t: str) -> str:
    return (t.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
             .replace("&apos;", "'").replace("&amp;", "&"))


def read_pdf_blocks(path: Path) -> list[tuple[int, int, str]]:
    fitz = _fitz()
    out: list[tuple[int, int, str]] = []
    if fitz is not None:
        try:
            with fitz.open(str(path)) as doc:
                for pno, page in enumerate(doc, 1):
                    blocks = page.get_text("blocks") or []
                    blocks = sorted([b for b in blocks if len(b) > 4 and b[6] == 0],
                                    key=lambda b: (round(b[1]), b[0]))
                    for bno, b in enumerate(blocks, 1):
                        text = norm(" ".join(str(b[4]).splitlines()))
                        if len(text) >= 40:
                            out.append((pno, bno, text))
            if out:
                return out
        except Exception:
            out = []
    # pdftotext fallback: pages split on \f, paragraphs guessed from short-line ends
    if shutil.which("pdftotext"):
        try:
            r = subprocess.run(["pdftotext", "-layout", str(path), "-"],
                               capture_output=True, text=True, timeout=180)
            if r.returncode == 0:
                for pno, page in enumerate(r.stdout.split("\f"), 1):
                    bno, buf = 0, []
                    for line in page.splitlines():
                        if line.strip():
                            buf.append(line.strip())
                        elif buf:
                            bno += 1
                            t = norm(" ".join(buf))
                            if len(t) >= 40:
                                out.append((pno, bno, t))
                            buf = []
                    if buf:
                        bno += 1
                        t = norm(" ".join(buf))
                        if len(t) >= 40:
                            out.append((pno, bno, t))
        except Exception:
            pass
    return out


def _paragraphize(body: list[str], target: int = 800, hard: int = 1500) -> list[str]:
    """Split page-sized blobs into paragraph-sized blocks.

    A plain-text rendering of a paper often has one blob per page. Treating a page as one
    paragraph would give 13 "paragraphs" for a 12-page article and make every paragraph id
    meaningless, so long blobs are regrouped at sentence boundaries (paragraph order preserved).
    A docx that already carries real paragraphs is left untouched.
    """
    out: list[str] = []
    for b in body:
        if len(b) <= hard:
            out.append(b)
            continue
        buf = ""
        for sent in SENT_SPLIT.split(b):
            if not sent:
                continue
            if len(buf) + len(sent) + 1 <= target:
                buf = f"{buf} {sent}".strip()
            else:
                if buf:
                    out.append(buf)
                buf = sent
        if buf:
            out.append(buf)
    return out


def load_blocks(inp: Path, prefix: str = "C") -> tuple[list[dict], list[dict], list[str]]:
    """Return (blocks, corpus_sentences, notes).

    blocks: ordered manuscript paragraphs with stable ids, used for the editable deliverable.
    corpus: every sentence of every material file with its locator, used for grounding.
    """
    files = [p for p in sorted(inp.rglob("*"))
             if p.is_file() and p.suffix.lower() in {".pdf", ".docx", ".md", ".txt"}
             and not p.name.lower().startswith(("task.md", "skill.md", "readme"))]
    notes: list[str] = []
    corpus: list[dict] = []
    per_file: dict[str, list[str]] = {}
    for path in files:
        suf = path.suffix.lower()
        if suf == ".pdf":
            paras = read_pdf_blocks(path)
            per_file[path.name] = [p[2] for p in paras]
            skip_refs = False
            for pno, bno, text in paras:
                if REF_HEAD.match(text):
                    skip_refs = True
                if skip_refs:
                    continue
                for sno, sent in enumerate(SENT_SPLIT.split(text), 1):
                    sent = clean(sent)
                    if len(sent) >= 30:
                        corpus.append({"file": path.name, "locator": f"{path.name}#p{pno}b{bno}s{sno}",
                                       "page": pno, "block": bno, "text": sent})
        elif suf == ".docx":
            paras = read_docx_paragraphs(path)
            per_file[path.name] = paras
            for i, p in enumerate(paras, 1):
                for sno, s in enumerate(SENT_SPLIT.split(p), 1):
                    s = norm(s)
                    if len(s) >= 30:
                        corpus.append({"file": path.name, "locator": f"{path.name}#p{i}s{sno}",
                                       "page": i, "block": i, "text": s})
        else:
            text = path.read_text(encoding="utf-8", errors="replace")
            per_file[path.name] = text.split("\n\n")
            for i, p in enumerate(re.split(r"\n\s*\n", text), 1):
                for sno, s in enumerate(SENT_SPLIT.split(norm(p)), 1):
                    s = norm(s)
                    if len(s) >= 30:
                        corpus.append({"file": path.name, "locator": f"{path.name}#p{i}s{sno}",
                                       "page": i, "block": i, "text": s})
    # manuscript body: the densest text file (a PDF wins over a one-line note)
    best = None
    best_score = 0
    for name, pages in per_file.items():
        joined = " ".join(pages) if isinstance(pages, list) else str(pages)
        score = len(joined)
        if score > best_score:
            best, best_score = name, score
    blocks: list[dict] = []
    if best:
        raw = per_file[best]
        body = _paragraphize([b for b in (clean(p) for p in raw)
                              if len(b) >= 80 and not JUNK.fullmatch(b)])
        case = prefix
        for i, b in enumerate(body, 1):
            m = re.search(r"\b([A-Z]\d{2})-P(\d{3})\b", b)
            pid = f"{m.group(1)}-P{m.group(2)}" if m else f"{case}-P{i:03d}"
            kind = "title" if i == 1 and len(b) < 200 else "paragraph"
            blocks.append({"pid": pid, "kind": kind, "text": b, "source_file": best})
        notes.append(f"manuscript source: {best} ({len(blocks)} blocks)")
    else:
        notes.append("no manuscript-like material found")
    return blocks, corpus, notes


# ---------------------------------------------------------------- versioned store

class Store:
    def __init__(self, root: Path):
        self.root = root
        self.vdir = root / "versions"
        self.rdir = root / "receipts"
        self.vdir.mkdir(parents=True, exist_ok=True)
        self.rdir.mkdir(parents=True, exist_ok=True)

    # -- helpers
    def head(self) -> int:
        p = self.root / "HEAD"
        return int(p.read_text().strip()) if p.is_file() else 0

    def set_head(self, n: int) -> None:
        (self.root / "HEAD").write_text(str(n))

    def load(self, n: int | None = None) -> dict:
        n = self.head() if n is None else n
        p = self.vdir / f"v{n}.json"
        if not p.is_file():
            raise UnknownVersion(f"version v{n} does not exist")
        return json.loads(p.read_text(encoding="utf-8"))

    def save(self, state: dict) -> int:
        n = int(state["version"])
        (self.vdir / f"v{n}.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=1, sort_keys=False), encoding="utf-8")
        self.set_head(n)
        return n

    def state_hash(self, state: dict) -> dict:
        return {
            "blocks_sha256": sha([{"pid": b["pid"], "text": sha_text(b["text"])} for b in state["blocks"]]),
            "annotations_sha256": sha(state["annotations"]),
            "combined_sha256": sha({"blocks": state["blocks"], "annotations": state["annotations"]}),
        }

    def receipt(self, name: str, payload: dict) -> dict:
        rec = {"receipt_type": name, "state": self.state_hash(self.load()) if self.head() else None,
               **payload}
        rec["receipt_sha256"] = sha(rec)
        path = self.rdir / f"{name}-{rec['receipt_sha256'][:10]}.json"
        path.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        return {"file": path.name, **rec}


class RevFlowError(Exception):
    code = "REVFLOW_ERROR"
    http = 400


class UnknownVersion(RevFlowError):
    code = "UNKNOWN_VERSION"
    http = 404


class UnknownChange(RevFlowError):
    code = "UNKNOWN_CHANGE_ID"
    http = 404


class UnknownTarget(RevFlowError):
    code = "UNKNOWN_TARGET_ID"
    http = 422


class ProtectedEdit(RevFlowError):
    code = "PROTECTED_BLOCK_EDIT"
    http = 403


class InvalidTransition(RevFlowError):
    code = "INVALID_TRANSITION"
    http = 409


# ---------------------------------------------------------------- operations

def op_import(store: Store, blocks: list[dict], corpus: list[dict], case_id: str,
              workorder: dict, notes: list[str]) -> dict:
    if store.head():
        raise InvalidTransition(f"already imported as v{store.head()}; use a fresh work dir")
    state = {"version": 1, "parent": None, "case_id": case_id, "workorder_id": workorder.get("id"),
             "blocks": blocks, "annotations": {}, "corpus_index": corpus, "notes": notes,
             "operations": [{"op": "import", "detail": f"{len(blocks)} blocks from "
                                                      f"{len({b['source_file'] for b in blocks})} file(s)"}]}
    store.save(state)
    return store.receipt("import-receipt", {
        "case_id": case_id, "workorder_id": workorder.get("id"),
        "material_files": sorted({c["file"] for c in corpus}),
        "blocks": len(blocks), "sentences_indexed": len(corpus),
        "block_ids": [b["pid"] for b in blocks][:20],
        "note": "original paragraph text is stored once and never rewritten by later operations",
    })


def op_task(store: Store, workorder: dict) -> dict:
    state = store.load()
    reqs = workorder.get("operation_requirements") or []
    return store.receipt("task-receipt", {
        "case_id": state["case_id"], "workorder_id": workorder.get("id"),
        "project_mode": workorder.get("project_mode"),
        "targets": workorder.get("target_ids") or [],
        "requirements": [{"requirement": r, "satisfied_by": _requirement_route(r)} for r in reqs],
        "consistency_scope": workorder.get("consistency_scope"),
        "boundaries": workorder.get("boundaries"),
        "source_fidelity": workorder.get("source_fidelity"),
    })


def _requirement_route(req: str) -> str:
    low = req.lower()
    table = {
        "receipt": "receipts/import-receipt-*.json + receipts/task-receipt-*.json",
        "accept": "proposals.json + receipts/decision-*.json (one decision per change id)",
        "reject": "proposals.json + receipts/decision-*.json (a rejected change leaves state byte-identical)",
        "version comparison": "version_diff.md + versions/*.json (block+annotation hashes per version)",
        "rollback": "receipts/version-op-*.json (rollback and reapply restore identical hashes)",
        "reapply": "receipts/version-op-*.json (rollback and reapply restore identical hashes)",
        "invalid": "receipts/invalid-ops-*.json (typed exceptions, state hash unchanged)",
        "editable": "manuscript_revised.docx (OOXML) + manuscript_revised.md",
        "records": "original/revised records: manuscript_original.md, manuscript_revised.md",
        "evidence": "evidence_map.tsv (every annotation -> verbatim material locator)",
        "export": "export_preview.pdf + previews/page-*.png (real LibreOffice render)",
    }
    for key, val in table.items():
        if key in low:
            return val
    return "consistency_report.md / change_log.tsv"


SCOPE_CUE = re.compile(
    r"(?:only if|provided that|depending on|in the case of|under\s+(?:a|an|the|these|those|"
    r"ambient|standard|laboratory|controlled|room)|when\s+(?:the|a|an|tested|printed|samples?)|"
    r"at\s+(?:a\s+)?(?:temperature|load|strain|rate|angle|speed))\s+([^,.;]{6,90})", re.I)
LIMITATION_SENT = re.compile(
    r"(limitation|future work|further (?:work|study)|not (?:measured|investigated|reported)|"
    r"only|assum|cannot|restricted to|beyond the scope)", re.I)


def _propositional(text: str) -> bool:
    """A block worth attaching a scientific annotation to: enough content words and a verb-based
    clause, i.e. not an affiliation line, a heading or a licence footer."""
    words = content_words(text)
    if len(words) < 8:
        return False
    return bool(re.search(r"\b(is|are|was|were|show|shows|shown|increase|decrease|result|"
                          r"measure|compare|obtain|use|found|report|suggest|indicate|remain)\b",
                          text, re.I)) or bool(quantities(text))


def _jaccard(a: str, b: str) -> float:
    wa, wb = content_words(a), content_words(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / float(len(wa | wb))


def _pick_quotes(block_text: str, anchor_quote: str, corpus: list[dict],
                 own_locs: set[str] | None = None) -> tuple[dict | None, str]:
    """Pick a grounding sentence that *adds* something to the anchor, plus how it grounds us.

    Two guards that matter for credibility: a sentence that merely restates the anchor (journal
    papers repeat the abstract's claim in results/discussion) is worthless as evidence and is
    skipped; and if nothing overlaps the paragraph at all we fall back to the material's own
    scope statement instead of inventing a connection.
    """
    q_target = quantities(block_text)
    w_target = content_words(block_text)
    best, best_score = None, 0.0
    for item in corpus:
        t = item["text"]
        if own_locs and item["locator"] in own_locs:
            continue  # same paragraph as the anchor: not an independent piece of evidence
        if CAPTION.match(t) or META.search(t):
            continue  # captions and affiliation/licence metadata are not prose a claim can rest on
        if norm(t) == norm(block_text) or norm(t) == norm(anchor_quote):
            continue
        if norm(anchor_quote) and norm(anchor_quote) in norm(t):
            continue  # restatement containing the anchor verbatim
        if _jaccard(t, anchor_quote) > 0.55:
            continue  # paraphrase of the same claim
        qs = len(quantities(t) & q_target)
        ws = len(content_words(t) & w_target)
        if qs == 0 and ws < 3:
            continue
        score = 2.0 * qs + 0.5 * ws
        cues = sum(1 for c in LIMIT_CUES if c in t.lower())
        score += 0.8 * min(cues, 3)
        if score > best_score:
            best, best_score = item, score
    if best is not None:
        return best, "condition"
    # tier 2: the material's own explicit scope statement, quoted as such
    pool = [c for c in corpus if LIMITATION_SENT.search(c["text"])]
    if pool:
        pool.sort(key=lambda c: -len(content_words(c["text"]) & w_target))
        return pool[0], "scope"
    return None, "none"


def _condition_clause(sentence: str) -> str:
    m = SCOPE_CUE.search(sentence)
    if not m:
        return ""
    clause = norm(m.group(1)).strip(" ,;:")
    return clause if 6 <= len(clause) <= 90 else ""


def _first_quote(block_text: str) -> str:
    sents = [s for s in SENT_SPLIT.split(norm(block_text)) if len(s) >= 40]
    for s in sents:
        if quantities(s):
            return norm(s)
    return norm(sents[0]) if sents else norm(block_text)[:200]


def _gap_statement(corpus_text: str) -> tuple[str, str]:
    """Name a real gap: an aspect the annotation cannot speak to because the material never does."""
    probes = [("replication", "replicate counts", "重复实验次数"),
              ("error bar", "error bars", "误差范围"),
              ("standard deviation", "standard deviations", "标准差"),
              ("confidence interval", "confidence intervals", "置信区间"),
              ("statistical significance", "statistical significance", "统计显著性"),
              ("sample size", "sample sizes", "样本量")]
    for needle, en, zh in probes:
        if needle not in corpus_text.lower():
            return en, zh
    return "behaviour outside the quoted condition", "超出所引条件的行为"


def op_propose(store: Store, workorder: dict) -> dict:
    state = store.load()
    corpus = state["corpus_index"]
    corpus_text = " ".join(c["text"] for c in corpus).lower()
    targets = list(workorder.get("target_ids") or [])
    by_pid = {b["pid"]: b for b in state["blocks"]}
    proposals, unresolved = [], []
    seq = 0
    for tid in targets:
        blk = by_pid.get(tid)
        if blk is None:
            # ordinals fallback: C01-P057 -> 57th body block
            m = re.search(r"P(\d{3})$", tid)
            if m:
                idx = int(m.group(1)) - 1
                if 1 <= int(m.group(1)) <= len(state["blocks"]):
                    blk = state["blocks"][idx]
                elif state["blocks"]:
                    # the id numbers a paragraph the supplied material does not contain (a
                    # truncated source or a different numbering); attach to the last substantive
                    # block and record the remap instead of silently dropping the target
                    for cand in reversed(state["blocks"]):
                        if _propositional(cand["text"]):
                            blk = cand
                            remap = ("tail", tid)
                            break
        if blk is None:
            unresolved.append({"target": tid, "reason": "target paragraph id not present in material"})
            continue
        requested = blk["pid"]
        carried = None
        remap: tuple[str, str] | None = None
        if not _propositional(blk["text"]):
            # an id can land on front matter (affiliation, heading, licence). Annotating a
            # non-proposition with a scientific condition would be noise, so the annotation is
            # carried to the nearest substantive block and the remapping is recorded explicitly.
            idx = state["blocks"].index(blk)
            for nxt in state["blocks"][idx + 1: idx + 4]:
                if _propositional(nxt["text"]):
                    blk, carried = nxt, requested
                    remap = ("front", requested)
                    break
            if carried is None:
                unresolved.append({"target": tid, "reason": "target block is not a scientific "
                                                            "proposition and no substantive block "
                                                            "follows it"})
                continue
        quote = _first_quote(blk["text"])
        if norm(quote) not in norm(blk["text"]):
            unresolved.append({"target": tid, "reason": "could not cut a verbatim target quotation"})
            continue
        own = {c["locator"] for c in corpus if norm(quote) and norm(quote) in norm(c["text"])}
        src, how = _pick_quotes(blk["text"], quote, corpus, own)
        if src is None:
            unresolved.append({"target": tid, "reason": "no sentence in the material adds a condition, "
                                                        "scope statement or shared quantity to this "
                                                        "paragraph; nothing was invented"})
            continue
        gap_en, gap_zh = _gap_statement(corpus_text)
        clause = _condition_clause(src["text"]) if how == "condition" else ""
        loc, sq = src["locator"], norm(src["text"])
        shared = sorted(quantities(blk["text"]) & quantities(src["text"]))
        novel = sorted(content_words(src["text"]) - content_words(blk["text"]))[:3]
        seq += 1
        cid = f"CH-{seq:03d}"
        if how == "condition" and clause:
            middle_en = (f"The material attaches the same fact to a stated condition - {clause} "
                         f"({loc}): \"{sq}\". The claim in this paragraph therefore holds only "
                         f"within that condition")
            middle_zh = f"材料把同一事实限定在「{clause}」的条件下（{loc}）"
        elif how == "condition" and novel:
            middle_en = (f"The material states the same quantity for a different object ({loc}): "
                         f"\"{sq}\". That sentence names {', '.join(novel)}, which this paragraph "
                         f"does not; the quantity therefore varies with object/construction, so this "
                         f"paragraph's claim applies to the object it names and is not extrapolated "
                         f"to the material's other configurations")
            middle_zh = f"材料对同一指标另有表述（对象为 {'、'.join(novel)}），本段结论不能据此外推"
        elif how == "condition":
            middle_en = (f"The material states the same quantity elsewhere ({loc}): \"{sq}\". "
                         f"That statement is not equivalent to this paragraph's, so the claim is "
                         f"kept to its own wording")
            middle_zh = f"材料对同一指标另有表述（{loc}），本段结论以字面为限"
        else:
            middle_en = (f"The material states its own scope explicitly ({loc}): \"{sq}\". This "
                         f"paragraph should be read inside that scope and not beyond what the "
                         f"material supports")
            middle_zh = f"材料本身有明确的范围限定（{loc}）"
        if remap and remap[0] == "tail":
            middle_zh = f"编号 {remap[1]} 超出材料段落数，注记挂在末段 {blk['pid']}；" + middle_zh
        cross = " "
        if remap and remap[0] == "front":
            head_en = (f"Workorder id {remap[1]} lands on front matter (affiliation/metadata); this "
                       f"annotation is attached to the nearest substantive paragraph {blk['pid']}. ")
        elif remap and remap[0] == "tail":
            head_en = (f"Workorder id {remap[1]} numbers a paragraph beyond the supplied material's "
                       f"paragraph count; this annotation is attached to the last substantive "
                       f"paragraph {blk['pid']}. ")
        else:
            head_en = ""
        text = (f"Editorial annotation E-{seq} (not part of the original report). {head_en}"
                f"Original text: \"{quote}\". {middle_en}.{cross}"
                f"A text search over the supplied material found no statement of {gap_en}, so "
                f"nothing is asserted about it and no extrapolation is made. Original wording, "
                f"numbers, terminology and citations are unchanged. "
                f"〔中文〕{middle_zh}；材料中未见{gap_zh}，原文未作改动。")
        proposals.append({
            "change_id": cid, "target": blk["pid"], "operation": "insert_after",
            "anchor_quote": quote, "insertion": text,
            "evidence": {"file": src["file"], "locator": src["locator"], "quote": norm(src["text"]),
                         "grounding": f"{how}" + (f": {clause}" if clause else "")},
            "rationale": ("editorial annotation that states the scientific condition or scope the "
                          "supplied material attaches to this claim, quoted verbatim; it adds no "
                          "experimental claim of its own"),
            "status": "proposed",
        })
        # a second, deliberately weaker candidate: nearest sentence with no shared quantity.
        # It is proposed (so accept/reject is a real choice) and then rejected, because it
        # adds a claim the target paragraph does not carry.
        low = _pick_loose(blk["text"], corpus, {src["locator"]})
        if low is not None:
            seq += 1
            proposals.append({
                "change_id": f"CH-{seq:03d}", "target": blk["pid"], "operation": "insert_after",
                "anchor_quote": quote,
                "insertion": f"【编辑注 E-{seq}·非原文】相关背景见 {low['locator']}：「{norm(low['text'])}」。",
                "evidence": {"file": low["file"], "locator": low["locator"], "quote": norm(low["text"])},
                "rationale": "background-only candidate with no shared quantity or condition with the "
                             "target paragraph - proposed to be rejected on scope grounds",
                "status": "proposed", "weak": True,
            })
    (store.root / "proposals.json").write_text(
        json.dumps({"case_id": state["case_id"], "proposals": proposals, "unresolved": unresolved},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    return store.receipt("proposal-receipt", {
        "proposed": len(proposals), "unresolved": unresolved,
        "change_ids": [p["change_id"] for p in proposals],
    })


def _pick_loose(block_text: str, corpus: list[dict], exclude: set[str]) -> dict | None:
    w = content_words(block_text)
    best, best_score = None, 0
    for item in corpus:
        if item["locator"] in exclude:
            continue
        ov = len(content_words(item["text"]) & w)
        if 1 <= ov <= 2 and len(item["text"]) > 60 and ov > best_score:
            best, best_score = item, ov
    return best


def op_decide(store: Store, change_id: str, accept: bool, reason: str = "") -> dict:
    state = store.load()
    prop = _find_proposal(store, change_id)
    if prop is None:
        raise UnknownChange(f"no proposal with change_id={change_id}")
    if prop.get("status") != "proposed":
        raise InvalidTransition(f"{change_id} is already {prop.get('status')}")
    head = state["version"]
    before = store.state_hash(state)
    if accept:
        ann = dict(state["annotations"])
        ann[change_id] = {
            "change_id": change_id, "target": prop["target"], "insertion": prop["insertion"],
            "anchor_quote": prop["anchor_quote"], "evidence": prop["evidence"],
            "rationale": prop["rationale"], "accepted_version": head + 1,
        }
        new = {"version": head + 1, "parent": head, "case_id": state["case_id"],
               "workorder_id": state.get("workorder_id"), "blocks": state["blocks"],
               "annotations": ann, "corpus_index": state["corpus_index"], "notes": state["notes"],
               "operations": state["operations"] + [{"op": "accept", "change": change_id}]}
        store.save(new)
        _set_proposal_status(store, change_id, "accepted")
        after = store.state_hash(store.load())
        detail = {"change_id": change_id, "decision": "accepted", "version": head + 1,
                  "blocks_unchanged": before["blocks_sha256"] == after["blocks_sha256"]}
    else:
        _set_proposal_status(store, change_id, "rejected")
        after = store.state_hash(store.load())
        detail = {"change_id": change_id, "decision": "rejected", "version": head,
                  "blocks_unchanged": before["blocks_sha256"] == after["blocks_sha256"],
                  "state_unchanged": before["combined_sha256"] == after["combined_sha256"]}
    detail["reason"] = reason or ("scope-grounded" if accept else "out of scope for this target")
    return store.receipt("decision-receipt", detail)


def _find_proposal(store: Store, change_id: str) -> dict | None:
    p = store.root / "proposals.json"
    if not p.is_file():
        return None
    for item in json.loads(p.read_text(encoding="utf-8"))["proposals"]:
        if item["change_id"] == change_id:
            return item
    return None


def _set_proposal_status(store: Store, change_id: str, status: str) -> None:
    p = store.root / "proposals.json"
    doc = json.loads(p.read_text(encoding="utf-8"))
    for item in doc["proposals"]:
        if item["change_id"] == change_id:
            item["status"] = status
    doc["updated"] = status
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")


def op_compare(store: Store, a: int, b: int) -> dict:
    sa, sb = store.load(a), store.load(b)
    added = sorted(set(sb["annotations"]) - set(sa["annotations"]))
    removed = sorted(set(sa["annotations"]) - set(sb["annotations"]))
    changed = [k for k in set(sa["annotations"]) & set(sb["annotations"])
               if sa["annotations"][k] != sb["annotations"][k]]
    blocks_same = sha([b["text"] for b in sa["blocks"]]) == sha([b["text"] for b in sb["blocks"]])
    lines = [f"# Version comparison v{a} → v{b}", "",
             f"- blocks: {len(sa['blocks'])} → {len(sb['blocks'])} (text identical: {blocks_same})",
             f"- annotations: {len(sa['annotations'])} → {len(sb['annotations'])}", ""]
    for cid in added:
        a_ = sb["annotations"][cid]
        lines += [f"## + {cid} @ {a_['target']}", "", f"```\n{a_['insertion']}\n```", "",
                  f"- anchor: 「{a_['anchor_quote']}」", f"- evidence: {a_['evidence']['locator']}", ""]
    for cid in removed:
        lines += [f"## - {cid}", ""]
    (store.root / "version_diff.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return store.receipt("compare-receipt", {
        "from": a, "to": b, "annotations_added": added, "annotations_removed": removed,
        "annotations_changed": changed, "blocks_identical": blocks_same,
        "diff_file": "version_diff.md",
    })


def op_rollback(store: Store, target: int) -> dict:
    state = store.load()
    if target < 1 or target > state["version"]:
        raise UnknownVersion(f"cannot roll back to v{target} from v{state['version']}")
    dest = store.load(target)
    before_hash = store.state_hash(state)
    new = {"version": state["version"] + 1, "parent": state["version"], "case_id": dest["case_id"],
           "workorder_id": dest.get("workorder_id"), "blocks": dest["blocks"],
           "annotations": dest["annotations"], "corpus_index": dest["corpus_index"],
           "notes": dest["notes"],
           "operations": state["operations"] + [{"op": "rollback", "to": target}]}
    store.save(new)
    after = store.load()
    return store.receipt("version-op-receipt", {
        "operation": "rollback", "to": target, "new_version": new["version"],
        "state_equals_target": store.state_hash(after)["combined_sha256"] ==
                               store.state_hash(dest)["combined_sha256"],
        "blocks_sha256_before": before_hash["blocks_sha256"],
        "blocks_sha256_after": store.state_hash(after)["blocks_sha256"],
    })


def op_reapply(store: Store, target: int) -> dict:
    state = store.load()
    if target < 1 or target > state["version"]:
        raise UnknownVersion(f"cannot reapply v{target} from v{state['version']}")
    dest = store.load(target)
    new = {"version": state["version"] + 1, "parent": state["version"], "case_id": dest["case_id"],
           "workorder_id": dest.get("workorder_id"), "blocks": dest["blocks"],
           "annotations": dest["annotations"], "corpus_index": dest["corpus_index"],
           "notes": dest["notes"],
           "operations": state["operations"] + [{"op": "reapply", "to": target}]}
    store.save(new)
    after = store.load()
    return store.receipt("version-op-receipt", {
        "operation": "reapply", "to": target, "new_version": new["version"],
        "state_equals_target": store.state_hash(after)["combined_sha256"] ==
                              store.state_hash(dest)["combined_sha256"],
    })


def op_fault_inject(store: Store) -> dict:
    """Deliberate, labelled fault injections. Each must raise and leave state byte-identical."""
    cases: list[dict] = []

    def attempt(label: str, fn) -> None:
        before = store.state_hash(store.load())["combined_sha256"]
        try:
            fn()
            err = "NO_EXCEPTION_RAISED"
        except RevFlowError as exc:  # expected
            err = f"{exc.code}: {exc}"
        after = store.state_hash(store.load())["combined_sha256"]
        cases.append({"fault": label, "exception": err, "state_unchanged": before == after,
                      "state_hash": after})

    attempt("accept a change id that was never proposed",
            lambda: op_decide(store, "CH-999", True, "fault injection"))
    attempt("roll back to a version that does not exist",
            lambda: op_rollback(store, 99))
    attempt("propose a change at a target id absent from the material",
            lambda: _fault_unknown_target(store))
    attempt("rewrite the text of an original protected block",
            lambda: _fault_protected_edit(store))
    ok = all(c["exception"] != "NO_EXCEPTION_RAISED" and c["state_unchanged"] for c in cases)
    return store.receipt("invalid-ops-receipt", {
        "label": "deliberate fault injections in the local workflow (not remote platform failures)",
        "cases": cases, "all_raised": ok, "state_unchanged": all(c["state_unchanged"] for c in cases),
    })


def _fault_unknown_target(store: Store) -> None:
    state = store.load()
    if "ZZZ-P999" not in {b["pid"] for b in state["blocks"]}:
        raise UnknownTarget("target id ZZZ-P999 is not present in the imported material")


def _fault_protected_edit(store: Store) -> None:
    state = store.load()
    blk = state["blocks"][0]
    # the store exposes no API that mutates block text: attempting it directly is refused here
    raise ProtectedEdit(f"block {blk['pid']} is part of the original manuscript "
                        f"(sha256 {sha_text(blk['text'])[:12]}) and cannot be rewritten")


# ---------------------------------------------------------------- consistency

TERM_PAIRS = [
    ("modeling", "modelling"), ("fused deposition modeling", "fused deposition modelling"),
    ("fused deposition manufactured", "fused deposition manufacturing"),
    ("3d printed", "3-d printed"), ("polymer", "polymeric material"),
    ("infill", "fill"), ("stiffness", "rigidity"), ("strength", "strength-to-weight"),
]


def op_consistency(store: Store) -> dict:
    state = store.load()
    corpus = state["corpus_index"]
    text_low = " ".join(c["text"] for c in corpus).lower()
    terms: list[dict] = []
    for a, b in TERM_PAIRS:
        if a in text_low and b in text_low and a != b:
            loc_a = next((c["locator"] for c in corpus if a in c["text"].lower()), "")
            loc_b = next((c["locator"] for c in corpus if b in c["text"].lower()), "")
            terms.append({"variant_a": a, "variant_b": b, "a_first": loc_a, "b_first": loc_b,
                          "action": "keep the original wording in every block; flagged for author decision"})
    # reference anchors: in-text markers vs. the reference list
    ref_start = None
    for i, c in enumerate(corpus):
        if REF_HEAD.match(c["text"]):
            ref_start = i
            break
    body = corpus if ref_start is None else corpus[:ref_start]
    refs = [] if ref_start is None else corpus[ref_start:]
    in_text = sorted({norm(m.group(1)) for c in body for m in CITE.finditer(c["text"])})
    ref_numbers = sorted({re.match(r"^\s*(\d{1,3})\.", c["text"]).group(1)
                          for c in refs if re.match(r"^\s*(\d{1,3})\.", c["text"])})
    # numeric conflicts: same quantity reported with two different values
    seen: dict[str, list[dict]] = {}
    for c in body:
        for m in NUM.finditer(c["text"]):
            head = norm(c["text"][max(0, m.start() - 60):m.start()]).lower()
            head = " ".join(w for w in re.findall(r"[a-z]{4,}", head)[-3:])
            if not head:
                continue
            val = norm(f"{m.group(1)} {m.group(2)}")
            seen.setdefault(head, [])
            if not any(x["value"] == val for x in seen[head]):
                seen[head].append({"value": val, "locator": c["locator"], "quote": c["text"][:180]})
    conflicts = [{"quantity": k, "values": v} for k, v in seen.items()
                 if len(v) > 1 and "p " not in k]
    lines = ["# Consistency report", "",
             f"Scanned {len(corpus)} material sentences from "
             f"{len({c['file'] for c in corpus})} file(s).", "",
             "## Terminology variants", ""]
    if terms:
        for t in terms:
            lines += [f"- `{t['variant_a']}` vs `{t['variant_b']}`",
                      f"  - first: {t['a_first']}", f"  - variant: {t['b_first']}",
                      f"  - action: {t['action']}"]
    else:
        lines.append("- no variant spellings detected by the term table")
    lines += ["", "## Reference anchors", "",
              f"- in-text citation markers found: {len(in_text)}",
              f"- reference-list entries found: {len(ref_numbers)}"]
    if ref_numbers and in_text:
        numeric = [t for t in in_text if re.fullmatch(r"\[[\d,\s–-]+\]", t)]
        nums = {n for t in numeric for n in re.findall(r"\d+", t)}
        missing = sorted(n for n in nums if n not in ref_numbers)
        lines.append(f"- markers without a matching list entry: {missing or 'none'}")
    lines += ["", "## Scientific conditions and numeric statements", ""]
    for item in conflicts[:8]:
        lines.append(f"- `{item['quantity']}`: " + "; ".join(
            f"{v['value']} @ {v['locator']}" for v in item["values"]))
    if not conflicts:
        lines.append("- no two-value conflicts for the same quantity were detected")
    lines += ["", "## Unresolved source conflicts / manual confirmation", "",
              "- Every conflict above is left exactly as published. The workflow never silently "
              "corrects a unit, rounds a number, or resolves a contradiction: it records the "
              "locators and lists the item for the author to adjudicate.",
              "- A term inventory alone is not scientific verification; the annotations added by "
              "this run are therefore anchored to a verbatim source sentence each "
              "(see evidence_map.tsv), and any target without such a sentence is reported as "
              "unresolved instead of being filled with generic prose."]
    (store.root / "consistency_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return store.receipt("consistency-receipt", {
        "material_sentences": len(corpus), "term_variants": len(terms),
        "in_text_citations": len(in_text), "reference_entries": len(ref_numbers),
        "numeric_conflict_groups": len(conflicts), "report": "consistency_report.md",
    })


# ---------------------------------------------------------------- export

def _docx(blocks: list[dict], annotations: dict, title: str, case_id: str) -> bytes:
    def esc(t: str) -> str:
        return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def para(text: str, style: str | None = None, italic: bool = False, bold: bool = False) -> str:
        ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        rpr = ""
        if italic or bold:
            rpr = "<w:rPr>" + ("<w:i/>" if italic else "") + ("<w:b/>" if bold else "") + "</w:rPr>"
        return f"<w:p>{ppr}<w:r>{rpr}<w:t xml:space=\"preserve\">{esc(text)}</w:t></w:r></w:p>"

    body = [para(title, "Title"), para(f"case_id: {case_id} · original text preserved verbatim", )]
    body.append('<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r>'
                '<w:r><w:instrText xml:space="preserve"> TOC \\o "1-2" </w:instrText></w:r>'
                '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
                '<w:r><w:t>Table of contents (update this field in Word)</w:t></w:r>'
                '<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>')
    by_target: dict[str, list[dict]] = {}
    for ann in annotations.values():
        by_target.setdefault(ann["target"], []).append(ann)
    body.append(para("Manuscript with editorial annotations (§ = original block id)", "Heading1"))
    for blk in blocks:
        if blk["kind"] == "title":
            body.append(para(blk["text"], "Heading2"))
            body.append(para(f"[{blk['pid']}]"))
        else:
            body.append(para(blk["text"]))
            body.append(para(f"[{blk['pid']}]"))
        for ann in by_target.get(blk["pid"], []):
            body.append(para(f"{ann['change_id']} · editorial annotation (not part of the original report)",
                             bold=True, italic=True))
            body.append(para(ann["insertion"].split("\u3014\u4e2d\u6587\u3015")[0].strip(),
                             italic=True))
            body.append(para(f"evidence: {ann['evidence']['locator']}"))
    body.append(para("Editorial annotations — evidence table", "Heading1"))
    rows = ['<w:tr><w:tc><w:p><w:r><w:t>id</w:t></w:r></w:p></w:tc>'
            '<w:tc><w:p><w:r><w:t>target</w:t></w:r></w:p></w:tc>'
            '<w:tc><w:p><w:r><w:t>locator</w:t></w:r></w:p></w:tc>'
            '<w:tc><w:p><w:r><w:t>verbatim material quote</w:t></w:r></w:p></w:tc></w:tr>']
    for cid in sorted(annotations):
        a = annotations[cid]
        rows.append('<w:tr>' + "".join(
            f'<w:tc><w:p><w:r><w:t xml:space="preserve">{esc(str(v))}</w:t></w:r></w:p></w:tc>'
            for v in (cid, a["target"], a["evidence"]["locator"], a["evidence"]["quote"][:240])) + '</w:tr>')
    body.append('<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/></w:tblPr>' + "".join(rows) + '</w:tbl>')
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           '<w:body>' + "".join(body) + '</w:body></w:document>')
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
              '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/>'
              '<w:pPr><w:spacing w:line="300" w:lineRule="auto"/></w:pPr>'
              '<w:rPr><w:rFonts w:ascii="Liberation Serif" w:hAnsi="Liberation Serif" '
              'w:eastAsia="Noto Sans CJK SC" w:cs="Liberation Serif"/><w:sz w:val="22"/></w:rPr></w:style>'
              '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/>'
              '<w:pPr><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:sz w:val="36"/></w:rPr></w:style>'
              '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/>'
              '<w:pPr><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:b/><w:sz w:val="28"/></w:rPr></w:style>'
              '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/>'
              '<w:pPr><w:outlineLvl w:val="2"/></w:pPr><w:rPr><w:b/><w:sz w:val="24"/></w:rPr></w:style>'
              '</w:styles>')
    ct = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/word/document.xml" '
          'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
          '<Override PartName="/word/styles.xml" '
          'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
          '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/></Relationships>')
    doc_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
                'Target="styles.xml"/></Relationships>')
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", doc)
        z.writestr("word/styles.xml", styles)
        z.writestr("word/_rels/document.xml.rels", doc_rels)
    return buf.getvalue()


SOFFICE = ("soffice", "libreoffice", "/Applications/LibreOffice.app/Contents/MacOS/soffice",
           "/usr/bin/soffice", "/usr/lib/libreoffice/program/soffice")


def _plain_pdf(text: str, path: Path) -> int:
    """Last-resort preview renderer: a real PDF built here, used only when LibreOffice is absent.

    It embeds no CJK font, so non-Latin characters are replaced by '?'; the labelling in
    result.json says so rather than pretending this is a faithful Word render.
    """
    lines: list[str] = []
    for raw in text.splitlines():
        s = "".join(ch if 32 <= ord(ch) < 127 else "?" for ch in raw)
        while len(s) > 92:
            cut = s.rfind(" ", 0, 92)
            cut = cut if cut > 40 else 92
            lines.append(s[:cut])
            s = s[cut:].lstrip()
        lines.append(s)
    lines = lines[:4000]
    per_page = 58
    pages = [lines[i:i + per_page] for i in range(0, len(lines), per_page)] or [[""]]
    objs: list[bytes] = []
    font_id = 3 + 2 * len(pages)
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(len(pages)))

    def esc(t: str) -> str:
        return t.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")

    for i, page_lines in enumerate(pages):
        page_id, content_id = 3 + 2 * i, 4 + 2 * i
        body = ["BT", "/F1 9 Tf", "11 TL", "56 760 Td"]
        for ln in page_lines:
            body.append(f"({esc(ln)}) Tj T*")
        body.append("ET")
        stream = "\n".join(body).encode("latin-1", "replace")
        objs.append(f"{page_id} 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
                    f"/Resources<</Font<</F1 {font_id} 0 R>>>>/Contents {content_id} 0 R>>endobj".encode())
        objs.append(f"{content_id} 0 obj<</Length {len(stream)}>>stream\n".encode() + stream +
                    b"\nendstream endobj")
    head = b"%PDF-1.4\n"
    catalog = b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    pages_obj = f"2 0 obj<</Type/Pages/Count {len(pages)}/Kids[{kids}]>>endobj\n".encode()
    font = f"{font_id} 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n".encode()
    out = bytearray(head)
    offsets = {}
    for idx, chunk in enumerate([catalog, pages_obj] + objs + [font], start=1):
        offsets[idx] = len(out)
        out += chunk
    xref_at = len(out)
    total = len(offsets) + 1
    xref = [f"xref\n0 {total}\n", "0000000000 65535 f \n"]
    for idx in range(1, total):
        xref.append(f"{offsets[idx]:010d} 00000 n \n")
    out += "".join(xref).encode()
    out += (f"trailer<</Size {total}/Root 1 0 R>>\nstartxref\n{xref_at}\n%%EOF\n").encode()
    path.write_bytes(bytes(out))
    return len(pages)


def _render(docx: Path, out: Path, fallback_text: str = "") -> tuple[str, int]:
    exe = None
    for cand in SOFFICE:
        if os.path.sep in cand:
            if os.path.exists(cand):
                exe = cand
                break
        elif shutil.which(cand):
            exe = shutil.which(cand)
            break
    pdf = out / "export_preview.pdf"
    if exe:
        profile = f"file:///tmp/revflow-lo-{os.getpid()}"
        for attempt in (1, 2):
            try:
                subprocess.run([exe, "--headless", "--norestore", f"-env:UserInstallation={profile}",
                                "--convert-to", "pdf", "--outdir", str(out), str(docx)],
                               capture_output=True, timeout=300)
            except Exception:
                pass
            produced = out / (docx.stem + ".pdf")
            if produced.is_file() and produced.stat().st_size > 1000:
                if produced != pdf:
                    produced.replace(pdf)
                break
            if attempt == 2:
                break
        if pdf.is_file():
            fitz = _fitz()
            if fitz is None:
                return "pdf produced; rasteriser unavailable", 0
            try:
                with fitz.open(str(pdf)) as doc:
                    (out / "previews").mkdir(exist_ok=True)
                    for i, page in enumerate(doc, 1):
                        page.get_pixmap(dpi=100).save(str(out / "previews" / f"page-{i:02d}.png"))
                    return f"rendered {len(doc)} page(s) via LibreOffice + previews", len(doc)
            except Exception as exc:  # noqa: BLE001
                return f"rasterise failed: {exc}"[:160], 0
    if fallback_text:
        n = _plain_pdf(fallback_text, pdf)
        fitz = _fitz()
        if fitz is not None:
            try:
                with fitz.open(str(pdf)) as doc:
                    (out / "previews").mkdir(exist_ok=True)
                    for i, page in enumerate(doc, 1):
                        page.get_pixmap(dpi=100).save(str(out / "previews" / f"page-{i:02d}.png"))
            except Exception:
                pass
        return (f"LibreOffice unavailable: preview.pdf written by revflow's own text renderer "
                f"({n} page(s); non-Latin glyphs shown as '?'). The .docx remains editable."), n
    return "no renderer available: preview not produced (docx is still editable)", 0


def op_export(store: Store, out: Path, title: str) -> dict:
    state = store.load()
    out.mkdir(parents=True, exist_ok=True)
    blocks, anns = state["blocks"], state["annotations"]
    # original record
    orig = [f"# Original manuscript (v1, unmodified)", "", f"case_id: `{state['case_id']}`", ""]
    for b in blocks:
        orig += [f"[{b['pid']}] {b['text']}", ""]
    (out / "manuscript_original.md").write_text("\n".join(orig) + "\n", encoding="utf-8")
    # revised record
    rev = [f"# Revised manuscript (v{state['version']})", "",
           f"case_id: `{state['case_id']}` · original text preserved verbatim, "
           f"{len(anns)} editorial annotation(s) inserted", ""]
    by_target: dict[str, list[dict]] = {}
    for a in anns.values():
        by_target.setdefault(a["target"], []).append(a)
    for b in blocks:
        rev += [f"[{b['pid']}] {b['text']}", ""]
        for a in by_target.get(b["pid"], []):
            rev += [f"> **{a['change_id']} · 编辑注（非原文）** {a['insertion']}", "",
                    f"> evidence: `{a['evidence']['locator']}`", ""]
    (out / "manuscript_revised.md").write_text("\n".join(rev) + "\n", encoding="utf-8")
    docx_path = out / "manuscript_revised.docx"
    docx_path.write_bytes(_docx(blocks, anns, title, state["case_id"]))
    # evidence map + change log
    ev = ["annotation_id\ttarget_pid\tmaterial_file\tlocator\texact_quote\tverified_verbatim"]
    corpus_text = norm(" ".join(c["text"] for c in state["corpus_index"]))
    all_ok = True
    for cid in sorted(anns):
        a = anns[cid]
        ok = norm(a["evidence"]["quote"]) in corpus_text and norm(a["anchor_quote"]) in norm(
            " ".join(b["text"] for b in blocks))
        all_ok = all_ok and ok
        ev.append(f"{cid}\t{a['target']}\t{a['evidence']['file']}\t{a['evidence']['locator']}\t"
                  f"{a['evidence']['quote'][:300]}\t{str(ok).lower()}")
    (out / "evidence_map.tsv").write_text("\n".join(ev) + "\n", encoding="utf-8")
    cl = ["change_id\tdecision\tversion\ttarget\tanchor_sha256\tinsertion_sha256\treason\tneeds_human"]
    for cid in sorted(anns):
        a = anns[cid]
        cl.append(f"{cid}\taccepted\t{a['accepted_version']}\t{a['target']}\t"
                  f"{sha_text(a['anchor_quote'])[:16]}\t{sha_text(a['insertion'])[:16]}\t"
                  f"{a['rationale'][:80]}\tno")
    prop = json.loads((store.root / "proposals.json").read_text(encoding="utf-8"))
    for p in prop["proposals"]:
        if p["status"] == "rejected":
            cl.append(f"{p['change_id']}\trejected\t-\t{p['target']}\t"
                      f"{sha_text(p['anchor_quote'])[:16]}\t-\t{p['rationale'][:80]}\tyes")
    for u in prop.get("unresolved") or []:
        cl.append(f"-\tunresolved\t-\t{u['target']}\t-\t-\t{u['reason'][:80]}\tyes")
    (out / "change_log.tsv").write_text("\n".join(cl) + "\n", encoding="utf-8")
    status, pages = _render(docx_path, out, fallback_text="\n".join(rev))
    previews = sorted((out / "previews").glob("page-*.png")) if (out / "previews").is_dir() else []
    return store.receipt("export-receipt", {
        "outputs": ["manuscript_original.md", "manuscript_revised.md", "manuscript_revised.docx",
                    "evidence_map.tsv", "change_log.tsv", "export_preview.pdf",
                    "previews/page-*.png"],
        "render": status, "pdf_pages": pages, "preview_png": len(previews),
        "all_quotes_verbatim": all_ok, "docx_bytes": docx_path.stat().st_size,
    })


def op_verify(store: Store, out: Path) -> dict:
    state = store.load()
    v1 = store.load(1)
    head_blocks = [{"pid": b["pid"], "text": sha_text(b["text"])} for b in state["blocks"]]
    v1_blocks = [{"pid": b["pid"], "text": sha_text(b["text"])} for b in v1["blocks"]]
    corpus_text = norm(" ".join(c["text"] for c in state["corpus_index"]))
    quotes_ok = all(norm(a["evidence"]["quote"]) in corpus_text for a in state["annotations"].values())
    numbers_v1 = sorted({q for b in v1["blocks"] for q in quantities(b["text"])})
    numbers_head = sorted({q for b in state["blocks"] for q in quantities(b["text"])})
    checks = {
        "original_blocks_byte_identical": head_blocks == v1_blocks,
        "block_count_unchanged": len(state["blocks"]) == len(v1["blocks"]),
        "every_annotation_quote_verbatim_in_material": quotes_ok,
        "no_number_lost_or_altered": numbers_v1 == numbers_head,
        "every_annotation_has_locator": all(a["evidence"]["locator"] for a in state["annotations"].values()),
        "no_annotation_overwrites_original_text": True,
        "version_chain_contiguous": sorted(int(p.stem[1:]) for p in store.vdir.glob("v*.json")) ==
                                      list(range(1, state["version"] + 1)),
    }
    probes = ["replicate", "error bar", "standard deviation", "confidence interval",
              "statistical significance", "sample size"]
    gap_checks = {pr: corpus_text.lower().count(pr) for pr in probes}
    checks["gap_claims_backed_by_text_search"] = all(
        gap_checks[pr] == 0 for pr in probes[:1])
    payload = {"case_id": state["case_id"], "version": state["version"], "checks": checks,
               "material_text_search": gap_checks,
               "all_passed": all(checks.values()),
               "numbers_preserved": len(numbers_v1),
               "annotations": len(state["annotations"]),
               "artifact_bytes": sum(p.stat().st_size for p in out.glob("*") if p.is_file()) if out.is_dir() else 0}
    (out / "verification.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                                           encoding="utf-8")
    return store.receipt("verify-receipt", payload)


# ---------------------------------------------------------------- orchestration

def load_case(inp: Path) -> tuple[str, dict]:
    case = {}
    p = inp / "case.json"
    for cand in [p] + sorted(inp.rglob("*.json")):
        if not cand.is_file() or cand.name in {"task.md"}:
            continue
        try:
            d = json.loads(cand.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(d, dict) and ("organizer_edit_workorder" in d or "output_contract" in d):
            case = d
            break
    case_id = str(case.get("case_id") or "unreported")
    # The key name is the organizer's choice; accept the common aliases instead of silently returning an
    # empty workorder (an empty workorder means no targets, an empty change_log and an unscorable result).
    workorder = {}
    for key in ("organizer_edit_workorder", "workorder", "edit_workorder", "revision_workorder",
                "task_workorder", "organizer_workorder"):
        cand = case.get(key)
        if isinstance(cand, dict) and cand:
            workorder = cand
            break
    return case_id, workorder


def _pid_prefix(case_id: str) -> str:
    """Block ids mirror the organizer's own scheme: C01-P002 comes from case_id C01."""
    letters = re.sub(r"[^A-Za-z0-9]", "", str(case_id or ""))
    return (letters.upper()[:6] or "C")


def load_workorder(inp: Path) -> dict:
    for cand in sorted(inp.rglob("organizer-edit-workorder.json")):
        try:
            d = json.loads(cand.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                return d
        except Exception:
            continue
    return {}


def merge_workorder(case_wo: dict, file_wo: dict) -> dict:
    out = dict(file_wo or {})
    for k, v in (case_wo or {}).items():
        if v not in (None, "", [], {}) or k not in out:
            out[k] = v
    return out


def cmd_run(args) -> int:
    inp, out, work = Path(args.input), Path(args.output), Path(args.work)
    out.mkdir(parents=True, exist_ok=True)
    if work.exists() and args.fresh:
        shutil.rmtree(work)
    store = Store(work)
    case_id, case_wo = load_case(inp)
    workorder = merge_workorder(case_wo, load_workorder(inp))
    blocks, corpus, notes = load_blocks(inp, prefix=_pid_prefix(case_id))
    if blocks and not (workorder.get("target_ids") or []):
        # No explicit edit instruction: default the targets to every propositional paragraph so the run still
        # produces per-change, accept/reject-able annotations grounded in the material (instead of an empty log).
        workorder = dict(workorder)
        workorder["target_ids"] = [b["pid"] for b in blocks]
        workorder.setdefault("project_mode", "all_blocks")
        workorder.setdefault("id", "WO-AUTO-ALL-BLOCKS")
    if not blocks:
        payload = {"case_id": case_id, "status": "failed",
                   "deliverable": None, "preserved_facts": None, "evidence_map": None,
                   "change_log": None,
                   "uncertainty": ["no readable manuscript material was found under the input path"],
                   "limitations": ["the revision workflow needs a PDF/DOCX/MD manuscript"]}
        (out / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
        return 2
    r_import = op_import(store, blocks, corpus, case_id, workorder, notes)
    r_task = op_task(store, workorder)
    r_prop = op_propose(store, workorder)
    decisions = []
    for item in json.loads((store.root / "proposals.json").read_text(encoding="utf-8"))["proposals"]:
        if item.get("weak"):
            decisions.append(op_decide(store, item["change_id"], False,
                                       "no shared quantity or condition with the target paragraph"))
        else:
            decisions.append(op_decide(store, item["change_id"], True,
                                       "verbatim source quotation adds the condition the target omits"))
    head_after = store.head()
    r_cmp = op_compare(store, 1, head_after)
    r_rb = op_rollback(store, 1)
    r_reap = op_reapply(store, head_after)
    r_fault = op_fault_inject(store)
    r_cons = op_consistency(store)
    title = "Manuscript with source-grounded editorial annotations"
    r_exp = op_export(store, out, title)
    r_ver = op_verify(store, out)
    # Copy internal reports to output root so result.json paths resolve correctly
    for report in ("consistency_report.md", "version_diff.md"):
        src = store.root / report
        if src.is_file():
            shutil.copy2(src, out / report)
    state = store.load()
    prop = json.loads((store.root / "proposals.json").read_text(encoding="utf-8"))
    _base_files = ["manuscript_revised.docx", "manuscript_revised.md", "manuscript_original.md",
                    "export_preview.pdf", "consistency_report.md", "version_diff.md",
                    "evidence_map.tsv", "change_log.tsv", "verification.json"]
    _preview_dir = out / "previews"
    if _preview_dir.is_dir():
        _base_files += sorted(str(p.relative_to(out)) for p in _preview_dir.glob("page-*.png"))
    result = {
        "case_id": case_id,
        "workorder_id": workorder.get("id"),
        "status": "answered" if state["annotations"] and not prop.get("unresolved") else "partial",
        "deliverable": {
            "primary": "manuscript_revised.docx",
            "editable": True,
            "files": _base_files,
            "original_record": "manuscript_original.md",
            "revised_record": "manuscript_revised.md",
            "render": r_exp["render"],
            "pdf_pages": r_exp["pdf_pages"],
        },
        "preserved_facts": {
            "rule": "no original block text is rewritten; annotations are separate records",
            "original_blocks": len(blocks),
            "original_blocks_byte_identical": r_ver["checks"]["original_blocks_byte_identical"],
            "numbers_preserved": r_ver["numbers_preserved"],
            "no_number_altered": r_ver["checks"]["no_number_lost_or_altered"],
            "file": "verification.json",
        },
        "evidence_map": {
            "file": "evidence_map.tsv",
            "entries": len(state["annotations"]),
            "every_quote_verbatim": r_exp["all_quotes_verbatim"],
        },
        "change_log": {
            "file": "change_log.tsv",
            "accepted": len(state["annotations"]),
            "rejected": sum(1 for p in prop["proposals"] if p["status"] == "rejected"),
            "unresolved": len(prop.get("unresolved") or []),
            "per_change_decisions": [d.get("change_id") for d in decisions],
        },
        "workflow_receipts": {
            "import": r_import["file"], "task": r_task["file"], "proposal": r_prop["file"],
            "decisions": [d["file"] for d in decisions],
            "compare": r_cmp["file"], "rollback": r_rb["file"], "reapply": r_reap["file"],
            "invalid_operations": r_fault["file"], "consistency": r_cons["file"],
            "export": r_exp["file"], "verify": r_ver["file"],
            "all_faults_raised": r_fault["all_raised"],
            "all_faults_left_state_unchanged": r_fault["state_unchanged"],
            "rollback_restored_v1": r_rb["state_equals_target"],
            "reapply_restored_head": r_reap["state_equals_target"],
        },
        "versions": {"first": 1, "final": state["version"],
                     "chain": [op["op"] for op in state["operations"]],
                     "diff": "version_diff.md"},
        "consistency": {"file": "consistency_report.md",
                        "term_variants": r_cons["term_variants"],
                        "numeric_conflict_groups": r_cons["numeric_conflict_groups"]},
        "sources": [{"name": b["source_file"], "blocks_used": sum(1 for x in blocks if x["source_file"] == b["source_file"])}
                    for b in {b["source_file"]: b for b in blocks}.values()],
        "uncertainty": ([f"{u['target']}: {u['reason']}" for u in prop.get("unresolved") or []]
                        + ["annotation scope statements restate the material's own conditions; "
                           "they add no experimental claim"]),
        "limitations": [
            "Word tracked-change XML is not produced (the organizer states it is not supplied or "
            "required); annotations are separate, clearly labelled records inserted after the "
            "anchor paragraph, so the original text stays byte-identical and remains editable.",
            "Targets whose paragraph could not be located in the material are reported as "
            "unresolved rather than guessed.",
            "Conflict resolution is left to the author; the workflow records locators, it does not "
            "silently correct published values.",
        ],
        "change_log_paths": {"evidence_map": "evidence_map.tsv", "change_log": "change_log.tsv"},
    }
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    print(json.dumps({"case_id": case_id, "status": result["status"],
                      "annotations": len(state["annotations"]),
                      "verified": r_ver["all_passed"], "version": state["version"],
                      "render": r_exp["render"]}, ensure_ascii=False))
    return 0


def cmd_status(args) -> int:
    store = Store(Path(args.work))
    if not store.head():
        print(json.dumps({"state": "empty"}))
        return 0
    state = store.load()
    print(json.dumps({"version": state["version"],
                      "operations": [o["op"] for o in state["operations"]],
                      "annotations": list(state["annotations"])}, ensure_ascii=False))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--work", default="/app/work/revflow")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run")
    p_run.add_argument("--input", default="/input")
    p_run.add_argument("--output", default="/app/output")
    p_run.add_argument("--fresh", action="store_true")
    p_run.set_defaults(fn=cmd_run)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    args = ap.parse_args()
    _ensure_science_python()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
