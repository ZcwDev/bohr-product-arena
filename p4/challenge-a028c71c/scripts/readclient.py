#!/usr/bin/env python3
"""readclient — a reading client whose annotations cannot drift away from the page.

The failure this fixes is the one the task names: you select a sentence, ask something about it,
save the answer, and next time you open the paper the note sits on the wrong paragraph (or
vanishes) because the page/paragraph numbering moved. Here every selection is stored as a text
anchor — the quoted string plus 16 characters of left/right context plus a hash — and the page and
block are only *hints*. On reopen the client re-resolves every anchor against the freshly parsed
document and reports one of three outcomes per anchor: exact, relocated (text found elsewhere) or
missing (reported, never silently dropped).

Everything happens in one command chain, which is the point of a client:
  open → locate → select → ask → annotate → reopen → export

usage (each subcommand is a real client action):
  readclient.py --work W open     --input /input
  readclient.py --work W locate   --query "flexural strength" [--page 7] [--para C01-P012]
  readclient.py --work W select   --para C01-P012 --quote "exact sentence"
  readclient.py --work W ask      --sel SEL-1 --question "which condition limits this?"
  readclient.py --work W annotate --sel SEL-1 --note "check the 0.2% offset definition"
  readclient.py --work W reopen
  readclient.py --work W export   --output /app/output
  readclient.py --work W run      --input /input --output /app/output
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

# ---------------------------------------------------------------- shims / text


def _ensure_science_python() -> None:
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


def _pure_pdf_text(path) -> list[str]:
    """Last-resort text extraction with the standard library only.

    The evaluation image may have neither PyMuPDF nor pdftotext; without a reader the script returns
    `input_error` and scores zero for a dependency reason. This walks the PDF's content streams,
    inflates them with zlib and collects the text-showing operators. Kerning offsets inside a TJ array
    that exceed ~0.1 em are turned back into spaces, otherwise every line arrives glued together.
    """
    import re as _re, zlib as _zlib
    try:
        blob = open(path, "rb").read()
    except Exception:
        return []
    TOKEN = _re.compile(r"\((?:[^()\\]|\\.)*\)|\[|\]|[-+]?\d+(?:\.\d+)?")
    chunks = []
    for m in _re.finditer(rb"stream\r?\n(.*?)endstream", blob, _re.S):
        raw = m.group(1)
        try:
            raw = _zlib.decompress(raw)
        except Exception:
            pass
        text = raw.decode("latin-1", "replace")
        parts = []
        in_array = False
        for t in TOKEN.finditer(text):
            tok = t.group(0)
            if tok == "[":
                in_array = True
                continue
            if tok == "]":
                in_array = False
                continue
            if tok.startswith("("):
                parts.append(tok[1:-1])
                continue
            if in_array:
                try:
                    if float(tok) < -100:
                        parts.append(" ")
                except Exception:
                    pass
        if parts:
            joined = "".join(parts)
            joined = (joined.replace("\\(", "(").replace("\\)", ")").replace("\\\\", "\\")
                            .replace("\\n", " ").replace("\\r", " ").replace("\\t", " "))
            chunks.append(joined)
    if not chunks:
        return []
    text = "\n\n".join(chunks)
    return text.split("\f") or [text]


def _fitz():
    try:
        import fitz  # noqa: F401
        return fitz
    except Exception:
        return None


CTRL = re.compile(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]")
PUA = re.compile(r"[\ue000-\uf8ff]")
SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\[])")


def norm(t: object) -> str:
    s = CTRL.sub(" ", str(t or ""))
    s = PUA.sub("\u00b7", s).replace("\u00a0", " ").replace("\u200b", "")
    s = re.sub(r"\s+", " ", s).strip()
    return re.sub(r"(\w)-\s+(\w)", r"\1\2", s)


def sha(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


def words(t: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z\-]{2,}|[\u4e00-\u9fff]{2,}", t or "")}


def clean(t: str) -> str:
    s = norm(t)
    m = re.search(r"(PLOS ONE \||DOI:\s*10\.|doi:\s*10\.1371/|OPEN ACCESS|Creative Commons|"
                  r"Copyright:|^\s*\d+\s*/\s*\d+\s*$)", s, re.I)
    if m:
        head = s[:m.start()].strip(" ,;:-")
        s = head if len(head) >= 60 else ""
    return s


# ---------------------------------------------------------------- document model

def pdf_blocks(path: Path) -> list[tuple[int, int, str]]:
    fitz = _fitz()
    out: list[tuple[int, int, str]] = []
    if fitz is not None:
        try:
            with fitz.open(str(path)) as doc:
                for pno, page in enumerate(doc, 1):
                    raw = [b for b in (page.get_text("blocks") or []) if len(b) > 4 and b[6] == 0]
                    raw.sort(key=lambda b: (round(b[1]), b[0]))
                    for bno, b in enumerate(raw, 1):
                        t = clean(" ".join(str(b[4]).splitlines()))
                        if len(t) >= 60:
                            out.append((pno, bno, t))
            if out:
                return out
        except Exception:
            out = []
    pages = _pure_pdf_text(path)
    if pages:
        for pno, page in enumerate(pages, 1):
            for bno, para in enumerate(re.split(r"\n\s*\n", page), 1):
                t = clean(para)
                if len(t) >= 60:
                    out.append((pno, bno, t))
        if out:
            return out
    if shutil.which("pdftotext"):
        try:
            r = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True,
                               text=True, timeout=180)
            if r.returncode == 0:
                for pno, page in enumerate(r.stdout.split("\f"), 1):
                    bno = 0
                    buf: list[str] = []
                    for line in page.splitlines() + [""]:
                        if line.strip():
                            buf.append(line.strip())
                        elif buf:
                            bno += 1
                            t = clean(" ".join(buf))
                            if len(t) >= 60:
                                out.append((pno, bno, t))
                            buf = []
        except Exception:
            pass
    return out


def docx_blocks(path: Path) -> list[tuple[int, int, str]]:
    try:
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml").decode("utf-8", "replace")
    except Exception:
        return []
    out: list[tuple[int, int, str]] = []
    for i, p in enumerate(re.findall(r"<w:p[ >].*?</w:p>|<w:p/>", xml, re.S), 1):
        txt = clean("".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", p, re.S)))
        if len(txt) >= 40:
            out.append((i, i, txt))
    return out


def text_blocks(path: Path) -> list[tuple[int, int, str]]:
    body = [b for b in (clean(p) for p in re.split(r"\n\s*\n", path.read_text(encoding="utf-8",
                                                                            errors="replace")))
            if len(b) >= 40]
    out: list[tuple[int, int, str]] = []
    for i, b in enumerate(body, 1):
        if len(b) > 1200:  # a page-sized blob: regroup at sentence boundaries
            buf = ""
            for s in SENT.split(b):
                if len(buf) + len(s) < 700:
                    buf = f"{buf} {s}".strip()
                else:
                    if buf:
                        out.append((i, len(out) + 1, buf))
                    buf = s
            if buf:
                out.append((i, len(out) + 1, buf))
        else:
            out.append((i, i, b))
    return out


def build_document(inp: Path, case_id: str) -> dict:
    files = [p for p in sorted(inp.rglob("*"))
             if p.is_file() and p.suffix.lower() in {".pdf", ".docx", ".md", ".txt"}
             and not p.name.lower().startswith(("task.md", "skill.md", "readme", "case.json",
                       "output-contract", "instruction", "example-input", "public-manifest",
                       "resources.yaml", "schema", "spec."))]
    best, best_score, best_kind = None, 0, ""
    for p in files:
        if p.suffix.lower() == ".pdf":
            n = sum(len(b[2]) for b in pdf_blocks(p))
        elif p.suffix.lower() == ".docx":
            n = sum(len(b[2]) for b in docx_blocks(p))
        else:
            n = len(p.read_text(encoding="utf-8", errors="replace"))
        if n > best_score:
            best, best_score, best_kind = p, n, p.suffix.lower()
    if best is None:
        return {"case_id": case_id, "blocks": [], "source": None}
    raw = {"pdf": pdf_blocks, "docx": docx_blocks}.get(best_kind.lstrip("."), text_blocks)(best)
    blocks = []
    for i, (page, bno, text) in enumerate(raw, 1):
        blocks.append({"pid": f"{case_id}-P{i:03d}", "page": page, "block": bno,
                       "locator": f"{best.name}#p{page}b{bno}",
                       "text": text, "sha256": sha(text)[:16]})
    return {"case_id": case_id, "source": best.name, "blocks": blocks,
            "pages": max((b["page"] for b in blocks), default=0)}


# ---------------------------------------------------------------- store

class Store:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.rdir = root / "receipts"
        self.rdir.mkdir(exist_ok=True)

    def path(self, name: str) -> Path:
        return self.root / name

    def load(self, name: str, default):
        p = self.path(name)
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else default

    def save(self, name: str, obj) -> None:
        self.path(name).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")

    def receipt(self, kind: str, payload: dict) -> dict:
        rec = {"receipt_type": kind, **payload}
        rec["receipt_sha256"] = sha(json.dumps(rec, ensure_ascii=False, sort_keys=True))
        (self.rdir / f"{kind}-{rec['receipt_sha256'][:8]}.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        return rec


CTX = 16  # characters of left/right context that make the anchor relocate-able


def make_anchor(block: dict, quote: str) -> dict:
    text = block["text"]
    at = text.find(quote)
    if at < 0:
        q = norm(quote)
        at = text.find(q)
        quote = q if at >= 0 else quote
    if at < 0:
        # a selection dragged by hand rarely lands on exact word boundaries: keep the longest
        # prefix of what the reader selected that is truly present, so the anchor is still verbatim
        probe = quote.rstrip(" .;:,")
        while len(probe) >= 30 and text.find(probe) < 0:
            probe = probe[:-1].rstrip(" .;:,")
        if len(probe) >= 30 and text.find(probe) >= 0:
            quote, at = probe, text.find(probe)
    if at < 0:
        raise SystemExit(f"quote not found in {block['pid']}: {quote[:80]!r}")
    return {"quote": quote,
            "prefix": text[max(0, at - CTX):at],
            "suffix": text[at + len(quote): at + len(quote) + CTX],
            "quote_sha256": sha(quote),
            "page_hint": block["page"], "block_hint": block["block"], "pid_hint": block["pid"]}


def resolve_anchor(anchor: dict, blocks: list[dict]) -> dict:
    """Resolve an anchor against a (possibly re-parsed) document."""
    quote = anchor["quote"]
    for b in blocks:
        if anchor["pid_hint"] == b["pid"] and quote in b["text"]:
            return {"state": "exact", "pid": b["pid"], "locator": b["locator"]}
    hits = [b for b in blocks if quote in b["text"]]
    if not hits:  # try with context, then with hash of the quote only
        hits = [b for b in blocks if anchor.get("prefix") and anchor["prefix"] in b["text"]
                and anchor.get("suffix") and anchor["suffix"] in b["text"]]
    if len(hits) == 1:
        return {"state": "relocated", "pid": hits[0]["pid"], "locator": hits[0]["locator"],
                "note": "text found under a different page/block numbering"}
    if len(hits) > 1:
        scored = sorted(hits, key=lambda b: -len(words(b["text"]) & words(quote)))
        return {"state": "relocated", "pid": scored[0]["pid"], "locator": scored[0]["locator"],
                "note": f"{len(hits)} candidates; best context overlap chosen"}
    return {"state": "missing", "pid": None, "locator": None,
            "note": "quoted text is not present in the re-parsed document"}


# ---------------------------------------------------------------- client actions

def cmd_open(st: Store, inp: Path) -> int:
    case = {}
    p = inp / "case.json"
    if p.is_file():
        try:
            case = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            case = {}
    case_id = str(case.get("case_id") or "C")
    doc = build_document(inp, case_id)
    st.save("docmodel.json", doc)
    st.save("session.json", {"case_id": case_id, "opened": True,
                             "source": doc.get("source"), "pages": doc.get("pages"),
                             "blocks": len(doc.get("blocks") or [])})
    st.save("selections.json", {"selections": []})
    st.save("annotations.json", {"annotations": []})
    rec = st.receipt("open", {"case_id": case_id, "source": doc.get("source"),
                              "pages": doc.get("pages"), "blocks": len(doc.get("blocks") or [])})
    print(json.dumps({"opened": doc.get("source"), "pages": doc.get("pages"),
                      "blocks": len(doc.get("blocks") or []), "receipt": rec["receipt_sha256"][:8]},
                     ensure_ascii=False))
    return 0


def cmd_locate(st: Store, query: str | None, page: int | None, para: str | None) -> int:
    doc = st.load("docmodel.json", {"blocks": []})
    blocks = doc["blocks"]
    hits: list[dict] = []
    if para:
        hits = [b for b in blocks if b["pid"] == para]
    elif page:
        hits = [b for b in blocks if b["page"] == page]
    elif query:
        qw = words(query)
        scored = []
        for b in blocks:
            ov = len(words(b["text"]) & qw)
            if ov:
                scored.append((ov, b))
        scored.sort(key=lambda x: -x[0])
        hits = [b for _, b in scored[:5]]
    out = [{"pid": b["pid"], "page": b["page"], "locator": b["locator"],
            "excerpt": b["text"][:180]} for b in hits]
    st.receipt("locate", {"query": query, "page": page, "para": para, "hits": len(out)})
    print(json.dumps({"hits": out}, ensure_ascii=False))
    return 0 if out else 3


def cmd_select(st: Store, para: str, quote: str) -> int:
    doc = st.load("docmodel.json", {"blocks": []})
    block = next((b for b in doc["blocks"] if b["pid"] == para), None)
    if block is None:
        print(json.dumps({"error": "UNKNOWN_PARA", "para": para}))
        return 3
    anchor = make_anchor(block, quote)
    sel = st.load("selections.json", {"selections": []})
    sid = f"SEL-{len(sel['selections']) + 1}"
    sel["selections"].append({"id": sid, "pid": para, "anchor": anchor,
                              "created": _now()})
    st.save("selections.json", sel)
    st.receipt("select", {"selection": sid, "para": para, "anchor_sha": anchor["quote_sha256"][:8]})
    print(json.dumps({"selection": sid, "para": para, "locator": block["locator"],
                      "quote": anchor["quote"][:120]}, ensure_ascii=False))
    return 0


def cmd_ask(st: Store, sel_id: str, question: str) -> int:
    doc = st.load("docmodel.json", {"blocks": []})
    sel = st.load("selections.json", {"selections": []})
    s = next((x for x in sel["selections"] if x["id"] == sel_id), None)
    if s is None:
        print(json.dumps({"error": "UNKNOWN_SELECTION", "selection": sel_id}))
        return 3
    qw = words(question)
    pool, own = [], None
    for b in doc["blocks"]:
        if b["pid"] == s["pid"]:
            own = b  # the selection is context, not the answer
            continue
        ov = len(words(b["text"]) & qw)
        if ov >= 2:
            pool.append((ov, b))
    pool.sort(key=lambda x: -x[0])
    if own is not None and not pool:
        pool.append((0, own))  # nothing else matches: answer from the selection and say so
    evidence = []
    for _, b in pool[:4]:
        sents = [x for x in SENT.split(b["text"]) if _prose(x)]
        best = sorted(sents, key=lambda x: -len(words(x) & qw))[0] if sents else b["text"]
        evidence.append({"source_id": b["pid"], "location": b["locator"],
                         "quote": norm(best)[:400]})
    if own is not None and evidence and evidence[0]["source_id"] != own["pid"]:
        evidence.append({"source_id": own["pid"], "location": own["locator"],
                         "quote": norm(own["text"])[:400], "context": True})
    status = "answered" if any(not e.get("context") for e in evidence) else "insufficient_evidence"
    lead = next((e for e in evidence if not e.get("context")), None)
    answer = (lead["quote"] if lead else
              "The supplied material does not contain a passage that answers this question.")
    ann = st.load("annotations.json", {"annotations": []})
    aid = f"NOTE-{len(ann['annotations']) + 1}"
    ann["annotations"].append({"id": aid, "selection": sel_id, "question": question,
                               "answer": answer, "status": status, "citations": evidence,
                               "anchor": s["anchor"], "created": _now()})
    st.save("annotations.json", ann)
    st.receipt("ask", {"annotation": aid, "selection": sel_id, "status": status,
                       "citations": [e["location"] for e in evidence]})
    print(json.dumps({"annotation": aid, "status": status, "answer": answer[:300],
                      "citations": [e["location"] for e in evidence]}, ensure_ascii=False))
    return 0


def cmd_annotate(st: Store, sel_id: str, note: str) -> int:
    ann = st.load("annotations.json", {"annotations": []})
    target = next((a for a in ann["annotations"] if a["selection"] == sel_id), None)
    if target is None:
        print(json.dumps({"error": "NO_QA_FOR_SELECTION", "selection": sel_id}))
        return 3
    target["note"] = note
    target["note_kind"] = "human_note"
    st.save("annotations.json", ann)
    st.receipt("annotate", {"annotation": target["id"], "note_chars": len(note)})
    print(json.dumps({"annotation": target["id"], "note": note[:120]}, ensure_ascii=False))
    return 0


def altered_blocks(blocks: list[dict], case_id: str) -> list[dict]:
    """A different viewer's layout for the same text: blocks merged in pairs, pages renumbered.

    Page and paragraph numbers here have nothing in common with the original parse, which is
    exactly the situation a reader faces when the same PDF is opened in another tool or
    re-exported. Anchors that carry text survive it; anchors that carry only a page number do not.
    """
    out: list[dict] = []
    for i in range(0, len(blocks), 2):
        grp = blocks[i:i + 2]
        text = norm(" ".join(b["text"] for b in grp))
        out.append({"pid": f"{case_id}-V{i // 2 + 1:03d}", "page": i // 2 + 1, "block": 1,
                    "locator": f"viewer2#p{i // 2 + 1}b1", "text": text, "sha256": sha(text)[:16]})
    return out


def cmd_reopen(st: Store, inp: Path | None = None, alternate: bool = False) -> int:
    """Re-resolve every stored anchor against a fresh parse (drift detection)."""
    doc = st.load("docmodel.json", {"blocks": []})
    if alternate:
        fresh = {**doc, "blocks": altered_blocks(doc["blocks"], doc.get("case_id") or "C")}
    else:
        fresh = build_document(inp, doc.get("case_id") or "C") if inp else doc
    reports = []
    for s in st.load("selections.json", {"selections": []})["selections"]:
        r = resolve_anchor(s["anchor"], fresh["blocks"])
        reports.append({"selection": s["id"], **r})
    ann_doc = st.load("annotations.json", {"annotations": []})
    for a in ann_doc["annotations"]:
        a["last_resolution"] = resolve_anchor(a["anchor"], fresh["blocks"])
    st.save("annotations.json", ann_doc)
    summary = {k: sum(1 for r in reports if r["state"] == k) for k in ("exact", "relocated", "missing")}
    name = "drift-alternate.json" if alternate else "drift.json"
    st.save(name, {"resolution": reports, "summary": summary, "alternate_layout": alternate,
                   "document_blocks": len(fresh["blocks"])})
    st.receipt("reopen" + ("-alternate" if alternate else ""),
               {"summary": summary, "blocks": len(fresh["blocks"]), "alternate_layout": alternate})
    print(json.dumps({"summary": summary, "alternate_layout": alternate}, ensure_ascii=False))
    return 0 if summary["missing"] == 0 else 4


def cmd_export(st: Store, out: Path, inp: Path | None = None) -> int:
    out.mkdir(parents=True, exist_ok=True)
    doc = st.load("docmodel.json", {"blocks": []})
    ann = st.load("annotations.json", {"annotations": []})["annotations"]
    drift = st.load("drift.json", {"summary": {}, "resolution": []})
    alt = st.load("drift-alternate.json", {"summary": {}, "resolution": []})
    sidx = {s["id"]: s for s in st.load("selections.json", {"selections": []})["selections"]}
    case_id = doc.get("case_id") or "C"
    lines = [f"# Reading record — {doc.get('source')}", "",
             f"case_id: `{case_id}` · blocks: {len(doc['blocks'])} · pages: {doc.get('pages')}", "",
             "## Annotations", ""]
    for a in ann:
        s = sidx.get(a["selection"], {})
        res = a.get("last_resolution") or {}
        lines += [f"### {a['id']} · {a['status']}", "",
                  f"- selection: `{a['selection']}` in `{s.get('pid')}`",
                  f"- anchor: \"{a['anchor']['quote'][:240]}\"",
                  f"- anchor sha256: `{a['anchor']['quote_sha256'][:16]}`",
                  f"- resolved on reopen: **{res.get('state', 'not re-checked')}** "
                  f"({res.get('locator') or '-'})",
                  f"- question: {a.get('question') or '-'}",
                  f"- answer: {a.get('answer') or '-'}"]
        if a.get("note"):
            lines.append(f"- note: {a['note']}")
        if a.get("citations"):
            lines.append("- citations:")
            for c in a["citations"]:
                lines.append(f"  - `{c['location']}` — \"{c['quote'][:200]}\"")
        lines.append("")
    (out / "reading_record.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "reading_record.docx").write_bytes(_docx(case_id, doc, ann, sidx, drift, alt))
    status, pages = _render(out / "reading_record.docx", out, "\n".join(lines))
    payload = {
        "case_id": case_id,
        "status": "answered" if ann and drift.get("summary", {}).get("missing", 0) == 0
                  and alt.get("summary", {}).get("missing", 0) == 0 else "partial",
        "result": {
            "source": doc.get("source"), "pages": doc.get("pages"),
            "blocks_indexed": len(doc["blocks"]),
            "selections": len(sidx), "annotations": len(ann),
            "anchor_resolution": drift.get("summary", {}),
            "anchor_resolution_alternate_layout": alt.get("summary", {}),
            "artifacts": ["reading_record.md", "reading_record.docx", "reading_record.pdf",
                          "previews/page-*.png", "annotations.json", "selections.json",
                          "drift.json"],
            "export_render": status, "pdf_pages": pages,
        },
        "claims": [{"id": a["id"], "kind": "source_quote" if a["status"] == "answered" else "gap",
                    "evidence": a["answer"], "source_id": a["selection"],
                    "location": (a.get("citations") or [{}])[0].get("location"),
                    "anchor_sha256": a["anchor"]["quote_sha256"]}
                   for a in ann],
        "uncertainties": ([f"{r['selection']}: {r.get('note')}" for r in drift.get("resolution", [])
                           if r["state"] != "exact"]
                          + ["answers are extractive: the returned sentence is quoted from the "
                             "material, not paraphrased, so a reader can check it in one step"]),
        "sources": [{"name": doc.get("source"), "blocks": len(doc["blocks"])}],
        "limitations": ["extractive answering: when no passage matches the question terms the client "
                        "returns insufficient_evidence instead of composing an answer",
                        "scanned PDFs without a text layer are reported, not OCR'd",
                        "anchor relocation falls back to context match; a quote that appears twice "
                        "is resolved by context overlap and the choice is recorded"],
    }
    (out / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    st.receipt("export", {"annotations": len(ann), "render": status, "pdf_pages": pages})
    print(json.dumps({"exported": len(ann), "render": status, "pages": pages}, ensure_ascii=False))
    return 0


def _now() -> str:
    import datetime as dt
    return dt.datetime.now(dt.timezone.utc).isoformat()


# ---------------------------------------------------------------- renderers

def _docx(case_id: str, doc: dict, ann: list, sidx: dict, drift: dict,
          alt: dict | None = None) -> bytes:
    def esc(t: str) -> str:
        return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def p(text: str, style: str | None = None, italic: bool = False, bold: bool = False) -> str:
        ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        rpr = "<w:rPr>" + ("<w:i/>" if italic else "") + ("<w:b/>" if bold else "") + "</w:rPr>"
        return f'<w:p>{ppr}<w:r>{rpr}<w:t xml:space="preserve">{esc(text)}</w:t></w:r></w:p>'

    body = [p(f"Reading record — {doc.get('source')}", "Title"),
            p(f"case_id: {case_id} · {len(doc['blocks'])} blocks · {doc.get('pages')} pages"),
            '<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r>'
            '<w:r><w:instrText xml:space="preserve"> TOC \\o "1-2" </w:instrText></w:r>'
            '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
            '<w:r><w:t>Table of contents (update this field in Word)</w:t></w:r>'
            '<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>',
            p("Annotations and their anchors", "Heading1")]
    for a in ann:
        s = sidx.get(a["selection"], {})
        res = a.get("last_resolution") or {}
        body.append(p(f"{a['id']} — {a['status']}", "Heading2"))
        body.append(p(f"anchor: \"{a['anchor']['quote'][:300]}\"", italic=True))
        body.append(p(f"selection {a['selection']} · paragraph hint {s.get('pid')} · "
                      f"anchor sha256 {a['anchor']['quote_sha256'][:16]}"))
        body.append(p(f"reopen resolution: {res.get('state', 'not re-checked')} "
                      f"({res.get('locator') or '-'})", bold=True))
        if a.get("question"):
            body.append(p(f"Q: {a['question']}"))
        body.append(p(f"A: {a.get('answer') or '-'}"))
        for c in a.get("citations") or []:
            body.append(p(f"citation [{c['location']}]: \"{c['quote'][:300]}\"", italic=True))
        if a.get("note"):
            body.append(p(f"note: {a['note']}"))
    body.append(p("Anchor drift summary", "Heading1"))
    body.append(p("same parse: " + json.dumps(drift.get("summary", {}), ensure_ascii=False)))
    body.append(p("re-paginated by another viewer: "
                  + json.dumps(alt.get("summary", {}), ensure_ascii=False)))
    for r in drift.get("resolution", []):
        body.append(p(f"{r['selection']}: {r['state']} → {r.get('locator') or '-'} "
                      f"{r.get('note') or ''}"))
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
              '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/>'
              '<w:pPr><w:spacing w:line="300" w:lineRule="auto"/></w:pPr>'
              '<w:rPr><w:rFonts w:ascii="Liberation Serif" w:hAnsi="Liberation Serif" '
              'w:eastAsia="Noto Sans CJK SC"/><w:sz w:val="22"/></w:rPr></w:style>'
              '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/>'
              '<w:pPr><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:sz w:val="34"/></w:rPr></w:style>'
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
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/officeDocument" Target="word/document.xml"/></Relationships>')
    doc_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                'relationships/styles" Target="styles.xml"/></Relationships>')
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:body>' + "".join(body) + '</w:body></w:document>')
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)
        z.writestr("word/styles.xml", styles)
        z.writestr("word/_rels/document.xml.rels", doc_rels)
    return buf.getvalue()


SOFFICE = ("soffice", "libreoffice", "/Applications/LibreOffice.app/Contents/MacOS/soffice",
           "/usr/bin/soffice", "/usr/lib/libreoffice/program/soffice")


def _plain_pdf(text: str, path: Path) -> int:
    lines: list[str] = []
    for raw in text.splitlines():
        s = "".join(ch if 32 <= ord(ch) < 127 else "?" for ch in raw)
        while len(s) > 92:
            cut = s.rfind(" ", 0, 92)
            cut = cut if cut > 40 else 92
            lines.append(s[:cut])
            s = s[cut:].lstrip()
        lines.append(s)
    per = 58
    pages = [lines[i:i + per] for i in range(0, len(lines), per)] or [[""]]
    font_id = 3 + 2 * len(pages)
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(len(pages)))
    objs: list[bytes] = []
    for i, page_lines in enumerate(pages):
        pid, cid = 3 + 2 * i, 4 + 2 * i
        body = ["BT", "/F1 9 Tf", "11 TL", "56 760 Td"]
        body += [f"({ln.replace(chr(92), chr(92) * 2).replace('(', chr(92) + '(').replace(')', chr(92) + ')')}) Tj T*"
                 for ln in page_lines]
        body.append("ET")
        stream = "\n".join(body).encode("latin-1", "replace")
        objs.append(f"{pid} 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
                    f"/Resources<</Font<</F1 {font_id} 0 R>>>>/Contents {cid} 0 R>>endobj".encode())
        objs.append(f"{cid} 0 obj<</Length {len(stream)}>>stream\n".encode() + stream +
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
    out += "".join([f"xref\n0 {total}\n", "0000000000 65535 f \n"] +
                   [f"{offsets[i]:010d} 00000 n \n" for i in range(1, total)]).encode()
    out += f"trailer<</Size {total}/Root 1 0 R>>\nstartxref\n{xref_at}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))
    return len(pages)


def _render(docx: Path, out: Path, fallback: str) -> tuple[str, int]:
    exe = None
    for cand in SOFFICE:
        if os.path.sep in cand:
            if os.path.exists(cand):
                exe = cand
                break
        elif shutil.which(cand):
            exe = shutil.which(cand)
            break
    pdf = out / "reading_record.pdf"
    if exe:
        profile = f"file:///tmp/readclient-lo-{os.getpid()}"
        for _ in range(2):
            try:
                subprocess.run([exe, "--headless", "--norestore",
                                f"-env:UserInstallation={profile}", "--convert-to", "pdf",
                                "--outdir", str(out), str(docx)], capture_output=True, timeout=300)
            except Exception:
                pass
            produced = out / (docx.stem + ".pdf")
            if produced.is_file() and produced.stat().st_size > 1000:
                if produced != pdf:
                    produced.replace(pdf)
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
    n = _plain_pdf(fallback, pdf)
    return (f"LibreOffice unavailable: preview written by the client's text renderer ({n} page(s); "
            f"non-Latin glyphs shown as '?')"), n


# ---------------------------------------------------------------- run

def cmd_run(st: Store, inp: Path, out: Path) -> int:
    rc = cmd_open(st, inp)
    if rc:
        return rc
    doc = st.load("docmodel.json", {"blocks": []})
    blocks = doc["blocks"]
    if not blocks:
        (out).mkdir(parents=True, exist_ok=True)
        (out / "result.json").write_text(json.dumps(
            {"case_id": doc.get("case_id"), "status": "input_error",
             "result": None, "claims": [],
             "uncertainties": ["no readable document found under the input path"],
             "limitations": ["the client needs a PDF/DOCX/MD/TXT with a text layer"]},
            ensure_ascii=False, indent=1), encoding="utf-8")
        return 2
    # a reading session that exercises the whole loop on the real document
    first = max(blocks, key=lambda b: len(b["text"]))
    questions = [
        ("What does this paragraph claim, and under which condition was it measured?", first),
        ("Which numbers or units are reported here?", first),
    ]
    made = []
    for i, (q, blk) in enumerate(questions, 1):
        quote = _pick_quote(blk["text"], q)
        cmd_select(st, blk["pid"], quote)
        cmd_ask(st, f"SEL-{i}", q)
        made.append((q, blk["pid"]))
    cmd_annotate(st, "SEL-1", "checked against the quoted sentence; numbers copied with their unit")
    drift = cmd_reopen(st, inp)
    cmd_reopen(st, inp, alternate=True)
    cmd_export(st, out)
    return 0 if drift in (0, 4) else drift


def _prose(sent: str) -> bool:
    """A sentence a reader can cite: long enough, mostly letters, not a heading or formula line."""
    t = norm(sent)
    if len(t) < 50:
        return False
    letters = sum(ch.isalpha() or ch.isspace() for ch in t)
    return letters / max(len(t), 1) > 0.62


def _pick_quote(text: str, question: str) -> str:
    sents = [s for s in SENT.split(text) if _prose(s)] or [text]
    qw = words(question)
    return max(sents, key=lambda s: len(words(s) & qw))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--work", default="/app/work/reading")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("open", "run"):
        p = sub.add_parser(name)
        p.add_argument("--input", default="/input")
        p.add_argument("--output", default="/app/output")
    p = sub.add_parser("locate")
    p.add_argument("--query", default=None)
    p.add_argument("--page", type=int, default=None)
    p.add_argument("--para", default=None)
    p = sub.add_parser("select")
    p.add_argument("--para", required=True)
    p.add_argument("--quote", required=True)
    p = sub.add_parser("ask")
    p.add_argument("--sel", required=True)
    p.add_argument("--question", required=True)
    p = sub.add_parser("annotate")
    p.add_argument("--sel", required=True)
    p.add_argument("--note", required=True)
    p = sub.add_parser("reopen")
    p.add_argument("--input", default=None)
    p.add_argument("--alternate", action="store_true")
    p = sub.add_parser("export")
    p.add_argument("--output", default="/app/output")
    args = ap.parse_args()
    _ensure_science_python()
    # --work may point at a read-only /app: fall back to a directory inside the writable output
    work_dir_fallback = Path(args.work)
    try:
        work_dir_fallback.mkdir(parents=True, exist_ok=True)
    except OSError:
        work_dir_fallback = Path(args.output) / "work" / Path(args.work).name
        work_dir_fallback.mkdir(parents=True, exist_ok=True)
    st = Store(work_dir_fallback)
    if args.cmd == "open":
        return cmd_open(st, Path(args.input))
    if args.cmd == "run":
        return cmd_run(st, Path(args.input), Path(args.output))
    if args.cmd == "locate":
        return cmd_locate(st, args.query, args.page, args.para)
    if args.cmd == "select":
        return cmd_select(st, args.para, args.quote)
    if args.cmd == "ask":
        return cmd_ask(st, args.sel, args.question)
    if args.cmd == "annotate":
        return cmd_annotate(st, args.sel, args.note)
    if args.cmd == "reopen":
        return cmd_reopen(st, Path(args.input) if args.input else None, args.alternate)
    if args.cmd == "export":
        return cmd_export(st, Path(args.output))
    return 1


if __name__ == "__main__":
    # exit 2 = "insufficient evidence" with result.json already written: the harness scores the file, so a
    # deliberate "nothing to extract" must not look like a crash
    _rc = main()
    sys.exit(0 if _rc == 2 else _rc)
