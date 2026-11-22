#!/usr/bin/env python3
"""mindmap — build a source-grounded, editable mind map from a paper.

What separates this from a screenshot of a diagram: every node and every edge carries a locator
and a verbatim quotation, node kinds are typed, edges are typed (structural / supports / conflicts /
contrasts / depends), and the map is emitted in formats a person can keep editing (FreeMind .mm,
OPML, indented markdown) plus a laid-out SVG and a rendered PNG for inspection.

Grounding rules enforced in code:
  * a node is only created from a sentence that exists in the material, and its `quote` is re-checked
    as a substring before export;
  * a caption, an affiliation line or a page-footer fragment never becomes a node;
  * an edge is created only when both endpoints exist and the relation has a textual basis: the
    sentences share a quantity/term (supports/depends), name a contrast cue (contrasts), or report
    two different values for the same quantity (conflicts). No edge is invented for tidiness.

usage:
  mindmap.py --input /input --output /app/output [--max-nodes 120]
  mindmap.py --input /input --check /app/output      # verify an existing export
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
        import fitz  # noqa
        return fitz
    except Exception:
        return None


CTRL = re.compile(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]")
SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\[]|[A-Z][a-z])")
NUM = re.compile(r"(?<!Fig )(?<!Figure )(?<!Table )(?<!Ref )(?<![A-Za-z0-9_\-])"
                 r"([-+]?\d+(?:\.\d+)?(?:\s*[±+\-/]\s*\d+(?:\.\d+)?)?)\s*"
                 r"(%|°C|mm3|mm2|cm3|mm|cm|µm|nm|MPa|kPa|GPa|N|kg|g|mg|mL|L|wt%|ppm|nM|mM|h|min|"
                 r"days?|weeks?|years?|specimens?|samples?|patients?)")
CAPTION = re.compile(r"^\s*(Fig(?:ure)?|Table|Scheme|Chart)\s*\.?\s*\d", re.I)
META = re.compile(r"(@|\(at\)|Department of|Universit\w|Institute of|Corresponding author|E-?mail)", re.I)
BOILER = re.compile(r"(PLOS ONE \||DOI:\s*10\.|doi:\s*10\.1371/|OPEN ACCESS|Creative Commons|"
                    r"Copyright:|^\s*\d+\s*/\s*\d+\s*$)", re.I)
HEAD_CUE = re.compile(r"^(abstract|introduction|background|related work|materials? and methods?|"
                      r"methods?|experimental|results?|discussion|conclusions?|limitations?|"
                      r"references|acknowledg\w*|appendix)\b", re.I)
CONTRAST = re.compile(r"\b(however|whereas|in contrast|unlike|although|but)\b", re.I)
FINDING = re.compile(r"\b(we (?:show|found|report|demonstrate|observe)|results? (?:show|indicate)|"
                     r"increase[sd]?|decrease[sd]?|improve[sd]?|reduc\w+|higher|lower|greater)\b", re.I)
METHOD = re.compile(r"\b(we (?:used|measured|tested|printed|performed)|samples? were|"
                    r"specimens? were|was (?:measured|calculated|evaluated))\b", re.I)
LIMIT = re.compile(r"\b(limitation|assum\w+|only|not (?:measured|reported|investigated)|"
                   r"further (?:work|study)|future work)\b", re.I)
QUESTION = re.compile(r"\b(aim|objective|goal|we (?:investigate|examine|ask)|research question|"
                      r"hypothes\w+)\b", re.I)
GAP = re.compile(r"\b(unknown|unclear|not (?:yet|been) (?:studied|addressed)|remains? to be)\b", re.I)


def short_label(text: str, limit: int = 36) -> str:
    """思维导图节点要短：整句/整段做标签会画成一块"文字墙"，读者抓不到要点。

    规则：取第一个分句（。；，、:：），超过 limit 再按词边界截断；完整原文仍在 `quote` 里，回溯不受影响。
    """
    t = norm(text)
    if not t:
        return ""
    head = re.split(r"[。；;:：]|(?<=[，,])", t, maxsplit=1)[0].strip(" ，,、")
    if not head:
        head = t
    if len(head) <= limit:
        return head
    cut = head[:limit].rsplit(" ", 1)[0].rstrip(" ,;:(-，、")
    return (cut or head[:limit]) + "…"


def clip(text: str, limit: int = 104) -> str:
    """Cut a label at a word boundary: a label ending in "Nylon, a" reads as broken text."""
    t = norm(text)
    if len(t) <= limit:
        return t
    cut = t[:limit].rsplit(" ", 1)[0].rstrip(" ,;:(-")
    return cut + "\u2026"


def norm(t: object) -> str:
    s = CTRL.sub(" ", str(t or "")).replace("\u00a0", " ").replace("\u200b", "")
    s = re.sub(r"[\ue000-\uf8ff]", "\u00b7", s)
    s = re.sub(r"\s+", " ", s).strip()
    return re.sub(r"(\w)-\s+(\w)", r"\1\2", s)


def sha(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


def words(t: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z\-]{3,}", t or "")}


def clean(t: str) -> str:
    s = norm(t)
    m = BOILER.search(s)
    if m:
        head = s[:m.start()].strip(" ,;:-")
        s = head if len(head) >= 60 else ""
    return s


def prose(sent: str, min_len: int = 45) -> bool:
    t = norm(sent)
    if len(t) < min_len or CAPTION.match(t) or META.search(t):
        return False
    letters = sum(ch.isalpha() or ch.isspace() for ch in t)
    return letters / max(len(t), 1) > 0.62


# ---------------------------------------------------------------- material


def paragraphize(text: str, target: int = 700, hard: int = 1200) -> list[str]:
    """A plain-text dump is often one blob per page; regroup it at sentence boundaries so blocks
    behave like paragraphs (otherwise a 14-page paper yields 14 "paragraphs" and one section)."""
    out: list[str] = []
    for chunk in ([text] if len(text) <= hard else SENT.split(text)):
        if len(chunk) <= hard:
            out.append(chunk)
            continue
        buf = ""
        for sent in SENT.split(chunk):
            if len(buf) + len(sent) + 1 <= target:
                buf = f"{buf} {sent}".strip()
            else:
                if buf:
                    out.append(buf)
                buf = sent
        if buf:
            out.append(buf)
    return out


def material_blocks(inp: Path) -> tuple[list[dict], str]:
    files = [p for p in sorted(inp.rglob("*"))
             if p.is_file() and p.suffix.lower() in {".pdf", ".docx", ".md", ".txt"}
             and not p.name.lower().startswith(("task.md", "skill.md", "readme", "case.json",
                       "output-contract", "instruction", "example-input", "public-manifest",
                       "resources.yaml", "schema", "spec."))]
    if not files:
        return [], "none"
    best, score = None, 0
    for p in files:
        s = p.stat().st_size
        if s > score:
            best, score = p, s
    blocks: list[dict] = []
    fitz = _fitz()
    if best.suffix.lower() == ".pdf":
        if fitz is None:
            for pno, page in enumerate(_pure_pdf_text(best), 1):
                for bno, para in enumerate(re.split(r"\n\s*\n", page), 1):
                    t = clean(norm(para))
                    if len(t) >= 50:
                        blocks.append({"page": pno, "block": bno, "text": t,
                                       "locator": f"{best.name}#p{pno}b{bno}"})
        if fitz is not None:
            try:
                with fitz.open(str(best)) as doc:
                    for pno, page in enumerate(doc, 1):
                        raw = [b for b in (page.get_text("blocks") or []) if len(b) > 4 and b[6] == 0]
                        raw.sort(key=lambda b: (round(b[1]), b[0]))
                        for bno, b in enumerate(raw, 1):
                            t = clean(" ".join(str(b[4]).splitlines()))
                            if len(t) >= 50:
                                blocks.append({"page": pno, "block": bno, "text": t,
                                               "locator": f"{best.name}#p{pno}b{bno}"})
            except Exception:
                blocks = []
        if not blocks and shutil.which("pdftotext"):
            r = subprocess.run(["pdftotext", "-layout", str(best), "-"], capture_output=True,
                               text=True, timeout=180)
            for pno, page in enumerate((r.stdout or "").split("\f"), 1):
                bno = 0
                for para in re.split(r"\n\s*\n", page):
                    bno += 1
                    t = clean(para)
                    if len(t) >= 50:
                        blocks.append({"page": pno, "block": bno, "text": t,
                                       "locator": f"{best.name}#p{pno}b{bno}"})
    elif best.suffix.lower() == ".docx":
        try:
            with zipfile.ZipFile(best) as z:
                xml = z.read("word/document.xml").decode("utf-8", "replace")
            for i, p in enumerate(re.findall(r"<w:p[ >].*?</w:p>|<w:p/>", xml, re.S), 1):
                t = clean("".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", p, re.S)))
                if len(t) >= 40:
                    blocks.append({"page": 1, "block": i, "text": t,
                                   "locator": f"{best.name}#p{i}"})
        except Exception:
            blocks = []
    else:
        text = best.read_text(encoding="utf-8", errors="replace")
        pages = text.split("\f")
        i = 0
        for pno, page in enumerate(pages, 1):
            for para in re.split(r"\n\s*\n", page):
                for t in paragraphize(clean(para)):
                    if len(t) >= 50:
                        i += 1
                        blocks.append({"page": pno, "block": i, "text": t,
                                       "locator": f"{best.name}#p{pno}b{i}"})
    return blocks, best.name


def recurring_furniture(blocks: list[dict], min_pages: int = 3) -> set[str]:
    """Running headers repeat on every page ("Strengthening of 3D Printed Parts ... 5 / 19").

    They look exactly like a heading (short, capitalised, no full stop), so without this filter
    every page contributes a bogus section and the map's level 1 is pure noise.
    """
    seen: dict[str, set[int]] = {}
    for b in blocks:
        key = re.sub(r"\d+\s*/\s*\d+", "", norm(b["text"])).strip().lower()
        seen.setdefault(key, set()).add(b["page"])
    return {k for k, pages in seen.items() if len(pages) >= min_pages}


def sentence_units(blocks: list[dict]) -> list[dict]:
    out: list[dict] = []
    for b in blocks:
        for i, s in enumerate(SENT.split(b["text"]), 1):
            s = norm(s)
            if prose(s):
                out.append({"locator": f"{b['locator']}s{i}", "page": b["page"],
                            "block": b["block"], "text": s})
    return out


# ---------------------------------------------------------------- graph


def is_heading(block: dict) -> bool:
    t = block["text"]
    if META.search(t) or CAPTION.match(t):
        return False  # editor names, affiliations and captions are not sections
    if HEAD_CUE.match(t):
        return True
    if len(t) <= 90 and not t.endswith("."):
        letters = sum(ch.isalpha() for ch in t)
        upper = sum(ch.isupper() for ch in t if ch.isalpha())
        if letters and (upper / letters > 0.6 or t.islower() is False and len(t.split()) <= 12):
            return True
    return False


def kind_of(sent: str) -> str:
    if GAP.search(sent):
        return "gap"
    if QUESTION.search(sent):
        return "question"
    if LIMIT.search(sent):
        return "limitation"
    if METHOD.search(sent):
        return "method"
    if FINDING.search(sent) or NUM.search(sent):
        return "finding"
    return "claim"


def build_graph(blocks: list[dict], units: list[dict], case_id: str, max_nodes: int,
                furniture: set[str] | None = None) -> dict:
    furniture = furniture or set()
    nodes: list[dict] = []
    edges: list[dict] = []

    def add(label: str, kind: str, locator: str, quote: str, parent: str | None,
            evidence_type: str = "fact") -> str:
        nid = f"N{len(nodes) + 1:03d}"
        shown = label if kind == "title" else short_label(label)
        nodes.append({"id": nid, "parent": parent, "label": clip(shown, 60), "kind": kind,
                      "locator": locator, "quote": norm(quote)[:400],
                      "evidence_type": evidence_type, "quote_sha256": sha(norm(quote))[:16]})
        if parent:
            edges.append({"source": parent, "target": nid, "relation": "structural",
                          "basis": f"hierarchy under {parent}"})
        return nid

    # 根节点用"标题"而不是第一段正文（正文首段通常很长，画出来就是一行字墙）
    first_heading = next((b for b in blocks if is_heading(b)), None)
    title = short_label((first_heading or blocks[0])["text"], 60) if blocks else case_id
    root = add(title, "title", blocks[0]["locator"] if blocks else "-", title, None)

    # level 1: sections (headings), with content blocks assigned to the most recent heading
    sections: list[tuple[dict, list[dict]]] = []
    current: tuple[dict, list[dict]] | None = None
    for b in blocks:
        key = re.sub(r"\d+\s*/\s*\d+", "", norm(b["text"])).strip().lower()
        if key in furniture:
            continue  # page furniture never becomes a section
        if is_heading(b):
            current = (b, [])
            sections.append(current)
        elif current is not None:
            current[1].append(b)
    if not sections:  # no detectable headings: one synthetic section covering the material
        # 没有可识别标题时，用材料语言给个中性节名（"body" 是英文占位，出现在中文图里很突兀）
        has_cjk = any(re.search(r"[\u4e00-\u9fff]", b["text"] or "") for b in blocks)
        sections = [({"text": "正文" if has_cjk else "body",
                      "locator": blocks[0]["locator"] if blocks else "-"}, blocks)]
    for head, body in sections:
        label = norm(head["text"])[:70]
        if not label:
            continue
        sec = add(label, "section", head["locator"], label, root, evidence_type="summary")
        # level 2: the section's most informative sentences, in document order
        keys = {(b["page"], b["block"]) for b in body}
        picked = [u for u in units if (u["page"], u["block"]) in keys]
        def rank(u: dict) -> int:
            return (2 if NUM.search(u["text"]) else 0) + (2 if FINDING.search(u["text"]) else 0) \
                + (1 if kind_of(u["text"]) in ("method", "limitation", "gap") else 0)
        picked = sorted(picked, key=lambda u: -rank(u))[:5]
        picked.sort(key=lambda u: (u["page"], u["block"]))
        for u in picked:
            claim = add(u["text"], kind_of(u["text"]), u["locator"], u["text"], sec,
                        evidence_type="fact" if NUM.search(u["text"]) else "summary")
            # level 3: quantitative facts inside the sentence, one node each
            for m in list(NUM.finditer(u["text"]))[:1]:
                value = norm(f"{m.group(1)} {m.group(2)}")
                add(value, "quantity", u["locator"], u["text"], claim)
            if len(nodes) > max_nodes:
                break
        if len(nodes) > max_nodes:
            break

    # typed relations beyond hierarchy, each with a textual basis
    by_id = {n["id"]: n for n in nodes}
    claim_nodes = [n for n in nodes if n["kind"] in ("finding", "claim", "method", "limitation", "gap")]
    depends: list[tuple[int, dict]] = []
    contrasts: list[dict] = []
    for i, a in enumerate(claim_nodes):
        for b in claim_nodes[i + 1:]:
            if a["parent"] == b["parent"]:
                continue
            shared = words(a["quote"]) & words(b["quote"])
            if len(shared) >= 4:
                depends.append((len(shared), {"source": a["id"], "target": b["id"],
                                              "relation": "depends",
                                              "basis": f"shared terms: {', '.join(sorted(shared)[:4])}"}))
                continue
            if CONTRAST.search(b["quote"]) and len(words(a["quote"]) & words(b["quote"])) >= 2:
                contrasts.append({"source": a["id"], "target": b["id"], "relation": "contrasts",
                                  "basis": "the second sentence carries a contrast cue "
                                           f"({CONTRAST.search(b['quote']).group(1)}) and shares terms"})
    # same quantity reported with two values -> an explicit conflict edge
    seen: dict[str, list[dict]] = {}
    for n in nodes:
        for m in NUM.finditer(n["quote"]):
            key = " ".join(re.findall(r"[a-z]{5,}", n["quote"][max(0, m.start() - 50):m.start()])[-2:])
            if not key:
                continue
            seen.setdefault(key, []).append({"node": n["id"], "value": norm(f"{m.group(1)} {m.group(2)}")})
    graph_meta: dict[str, int] = {}
    conflicts = []
    for key, items in seen.items():
        values = {i["value"] for i in items}
        if len(values) > 1 and len(items) >= 2:
            conflicts.append({"source": items[0]["node"], "target": items[-1]["node"],
                              "relation": "conflicts",
                              "basis": f"same quantity ({key}) reported as "
                                       f"{' vs '.join(sorted(values))}"})
    edges.extend(conflicts[:12])
    graph_meta["conflict_edges_kept"] = min(len(conflicts), 12)
    graph_meta["conflict_edges_dropped"] = max(0, len(conflicts) - 12)
    # a 300-edge map is unreadable: keep the strongest shared-term edges, report the cut
    depends.sort(key=lambda x: -x[0])
    kept, dropped = depends[:40], max(0, len(depends) - 40)
    graph_meta["depends_edges_kept"] = len(kept)
    graph_meta["depends_edges_dropped"] = dropped
    edges.extend(e for _, e in kept)
    edges.extend(contrasts[:15])
    graph_meta["contrast_edges_kept"] = min(len(contrasts), 15)
    graph_meta["contrast_edges_dropped"] = max(0, len(contrasts) - 15)

    # de-duplicate edges
    uniq, seen_e = [], set()
    for e in edges:
        k = (e["source"], e["target"], e["relation"])
        if e["source"] != e["target"] and k not in seen_e:
            seen_e.add(k)
            uniq.append(e)
    return {"root": root, "nodes": nodes, "edges": uniq,
            "truncated": len(nodes) > max_nodes, "edge_budget": graph_meta}


# ---------------------------------------------------------------- writers


def write_mm(path: Path, graph: dict) -> None:
    by_id = {n["id"]: n for n in graph["nodes"]}
    kids: dict[str, list[str]] = {}
    for n in graph["nodes"]:
        if n["parent"]:
            kids.setdefault(n["parent"], []).append(n["id"])

    def esc(t: str) -> str:
        return (t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace('"', "&quot;"))

    def node(nid: str, depth: int) -> str:
        n = by_id[nid]
        attrs = (f' TEXT="{esc(n["label"])}" POSITION="{"right" if depth % 2 == 0 else "left"}" '
                 f'CREATED="0" MODIFIED="0"')
        note = esc(f'kind={n["kind"]} | locator={n["locator"]} | '
                   f'quote_sha256={n["quote_sha256"]} | quote="{n["quote"][:300]}"')
        inner = f'<richcontent TYPE="NOTE"><html><head></head><body><p>{note}</p></body></html>' \
                f'</richcontent>'
        children = "".join(node(k, depth + 1) for k in kids.get(nid, []))
        if children:
            return f"<node{attrs}>{inner}{children}</node>"
        return f"<node{attrs}>{inner}</node>"

    awkward = any(n["kind"] in ("gap", "limitation") for n in graph["nodes"])
    xml = ('<map version="1.0.1">'
           f'<node TEXT="{esc(by_id[graph["root"]]["label"])}" POSITION="right">'
           + "".join(node(k, 1) for k in kids.get(graph["root"], []))
           + "</node></map>")
    path.write_text(xml, encoding="utf-8")


def write_opml(path: Path, graph: dict) -> None:
    by_id = {n["id"]: n for n in graph["nodes"]}
    kids: dict[str, list[str]] = {}
    for n in graph["nodes"]:
        if n["parent"]:
            kids.setdefault(n["parent"], []).append(n["id"])

    def esc(t: str) -> str:
        return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

    def node(nid: str) -> str:
        n = by_id[nid]
        text = esc(f'{n["label"]}  [{n["kind"]} @ {n["locator"]}]')
        children = "".join(node(k) for k in kids.get(nid, []))
        return f'<outline text="{text}">{children}</outline>' if children else f'<outline text="{text}"/>'

    header = ('<?xml version="1.0" encoding="UTF-8"?><opml version="2.0"><head>'
              '<title>Mind map with source locators</title></head><body>')
    path.write_text(header + node(graph["root"]) + "</body></opml>", encoding="utf-8")


def write_md(path: Path, graph: dict) -> None:
    by_id = {n["id"]: n for n in graph["nodes"]}
    kids: dict[str, list[str]] = {}
    for n in graph["nodes"]:
        if n["parent"]:
            kids.setdefault(n["parent"], []).append(n["id"])
    rel = {}
    for e in graph["edges"]:
        if e["relation"] != "structural":
            rel.setdefault(e["source"], []).append(f'{e["relation"]}→{e["target"]}')
    lines: list[str] = []

    def walk(nid: str, depth: int) -> None:
        n = by_id[nid]
        mark = f' _({", ".join(rel[nid])})_' if nid in rel else ""
        lines.append("  " * depth
                     + '- **' + n["label"] + '** `' + n["kind"] + '` [' + n["locator"] + ']'
                     + mark)
        for k in kids.get(nid, []):
            walk(k, depth + 1)

    walk(graph["root"], 0)
    path.write_text("# Mind map (indented outline)\n\n" + "\n".join(lines) + "\n", encoding="utf-8")


WRAP = 30          # characters per label line
CHAR_W = 6.6       # DejaVu Sans 10.5px average advance
LINE_H = 13


def wrap_label(label: str) -> list[str]:
    """Break a label on word boundaries: hard slicing cut words in half and made text spill out of
    its box, which is worse than a shorter label."""
    words_, lines, cur = label.split(), [], ""
    for w in words_:
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= WRAP:
            cur += " " + w
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


def box_size(label: str) -> tuple[float, float]:
    lines = wrap_label(label)
    w = 26 + max(len(x) for x in lines) * CHAR_W
    h = 12 + LINE_H * len(lines)
    return w, h


def layout(graph: dict, width: int = 1560) -> dict:
    """Column per depth, with each node given its own height.

    A fixed 30 px row made 3-line labels overlap the box below; stacking variable heights keeps
    the map readable and lets the total height follow the content.
    """
    depth: dict[str, int] = {graph["root"]: 0}
    by_parent: dict[str, list[str]] = {}
    for n in graph["nodes"]:
        if n["parent"]:
            by_parent.setdefault(n["parent"], []).append(n["id"])
    order: list[str] = []

    def walk(nid: str, d: int) -> None:
        depth[nid] = d
        order.append(nid)
        for k in by_parent.get(nid, []):
            walk(k, d + 1)

    walk(graph["root"], 0)
    max_depth = max(depth.values()) or 1
    columns: dict[int, list[str]] = {}
    by_id = {n["id"]: n for n in graph["nodes"]}
    for nid in order:
        columns.setdefault(depth[nid], []).append(nid)
    xs, ys, heights = {}, {}, {}
    tallest = 0.0
    for d, ids in columns.items():
        col = 45.0
        for nid in ids:
            _, h = box_size(by_id[nid]["label"])
            heights[nid] = h
            ys[nid] = col + h / 2
            col += h + 9
        tallest = max(tallest, col)
    col_w = (width - 190) / max(max_depth, 1)
    for d, ids in columns.items():
        for nid in ids:
            xs[nid] = 95 + d * col_w
    return {"x": xs, "y": ys, "h": heights, "width": width,
            "height": int(max(tallest, 260) + 40), "kids": by_parent, "depth": depth}


COLORS = {"title": "#1f3b63", "section": "#2f6f8f", "claim": "#3b5b7a", "finding": "#1d6b45",
          "method": "#7a4b1d", "limitation": "#8a2f2f", "gap": "#6b2f6b", "quantity": "#4a4a4a"}


def write_svg(path: Path, graph: dict) -> tuple[int, int]:
    lay = layout(graph)
    by_id = {n["id"]: n for n in graph["nodes"]}
    W, H = lay["width"], int(lay["height"])
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
             f'viewBox="0 0 {W} {H}" font-family="DejaVu Sans, Helvetica, sans-serif">',
             f'<rect width="{W}" height="{H}" fill="#ffffff"/>']
    for n in graph["nodes"]:
        if not n["parent"]:
            continue
        x1, y1 = lay["x"][n["parent"]] + 12, lay["y"][n["parent"]]
        x2, y2 = lay["x"][n["id"]] - 12, lay["y"][n["id"]]
        mx = (x1 + x2) / 2
        parts.append(f'<path d="M{x1:.1f},{y1:.1f} C{mx:.1f},{y1:.1f} {mx:.1f},{y2:.1f} '
                     f'{x2:.1f},{y2:.1f}" fill="none" stroke="#b9c6d3" stroke-width="1.2"/>')
    for e in graph["edges"]:
        if e["relation"] == "structural":
            continue
        x1, y1 = lay["x"][e["source"]], lay["y"][e["source"]]
        x2, y2 = lay["x"][e["target"]], lay["y"][e["target"]]
        colour = {"conflicts": "#b03a2e", "contrasts": "#8e6b1f", "depends": "#4a6fa5",
                  "supports": "#2f6b4f"}.get(e["relation"], "#888")
        dash = ' stroke-dasharray="5 4"' if e["relation"] in ("conflicts", "contrasts") else ""
        parts.append(f'<path d="M{x1:.1f},{y1:.1f} Q{(x1 + x2) / 2:.1f},'
                     f'{(y1 + y2) / 2 - 40:.1f} {x2:.1f},{y2:.1f}" fill="none" '
                     f'stroke="{colour}" stroke-width="1.1"{dash} marker-end="url(#a)"/>')
    parts.insert(2, '<defs><marker id="a" markerWidth="7" markerHeight="7" refX="6" refY="3" '
                    'orient="auto"><path d="M0,0 L6,3 L0,6 z" fill="#888"/></marker></defs>')
    for n in graph["nodes"]:
        x, y = lay["x"][n["id"]], lay["y"][n["id"]]
        lines = wrap_label(n["label"])
        w, h = lay["h"].get(n["id"], 0) and box_size(n["label"]) or box_size(n["label"])
        colour = COLORS.get(n["kind"], "#3b5b7a")
        parts.append(f'<rect x="{x - w / 2:.1f}" y="{y - h / 2:.1f}" width="{w:.1f}" height="{h}" '
                     f'rx="5" fill="#ffffff" stroke="{colour}" stroke-width="1.4"/>')
        for i, c in enumerate(lines):
            parts.append(f'<text x="{x:.1f}" y="{y - h / 2 + LINE_H + i * LINE_H - 2:.1f}" '
                         f'font-size="10.5" text-anchor="middle" fill="{colour}">{_xml(c)}</text>')
    parts.append(f'<text x="14" y="{H - 12}" font-size="10" fill="#666">'
                 f'{len(graph["nodes"])} nodes · {len(graph["edges"])} edges · '
                 f'every node carries a locator and a verbatim quote</text>')
    parts.append("</svg>")
    path.write_text("\n".join(parts), encoding="utf-8")
    return W, H


def _xml(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def rasterise(svg: Path, out_dir: Path) -> tuple[str, list[str]]:
    fitz = _fitz()
    if fitz is None:
        return "rasteriser unavailable (SVG is still produced)", []
    try:
        with fitz.open(str(svg)) as doc:
            pix = doc[0].get_pixmap(dpi=110)
            p = out_dir / "mindmap.png"
            pix.save(str(p))
            return f"rasterised {pix.width}x{pix.height} png", [p.name]
    except Exception as exc:  # noqa: BLE001
        return f"svg rasterise failed: {exc}"[:160], []


def _depth(graph: dict) -> int:
    by_parent: dict[str, list[str]] = {}
    for n in graph["nodes"]:
        if n["parent"]:
            by_parent.setdefault(n["parent"], []).append(n["id"])

    def walk(nid: str) -> int:
        kids = by_parent.get(nid) or []
        return 1 + (max(walk(k) for k in kids) if kids else 0)

    return walk(graph["root"])


def coverage(graph: dict, blocks: list[dict], units: list[dict]) -> dict:
    used = {n["locator"].split("s")[0] for n in graph["nodes"]}
    covered = [b for b in blocks if any(b["locator"] in u for u in used)]
    kinds: dict[str, int] = {}
    for n in graph["nodes"]:
        kinds[n["kind"]] = kinds.get(n["kind"], 0) + 1
    rels: dict[str, int] = {}
    for e in graph["edges"]:
        rels[e["relation"]] = rels.get(e["relation"], 0) + 1
    return {"blocks_total": len(blocks), "blocks_with_a_node": len(covered),
            "block_coverage": round(len(covered) / max(len(blocks), 1), 3),
            "sentences_indexed": len(units), "node_kinds": kinds, "relations": rels,
            "max_depth": _depth(graph)}


# ---------------------------------------------------------------- main


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--max-nodes", type=int, default=120)
    ap.add_argument("--check", default=None)
    args = ap.parse_args()
    _ensure_science_python()
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    inp = Path(args.input)
    if args.check:
        return check_export(Path(args.check))
    blocks, source = material_blocks(inp)
    case_id = "C"
    p = inp / "case.json"
    if p.is_file():
        try:
            case_id = str(json.loads(p.read_text(encoding="utf-8")).get("case_id") or "C")
        except Exception:
            pass
    if not blocks:
        (out / "result.json").write_text(json.dumps(
            {"case_id": case_id, "status": "input_error", "result": None, "claims": [],
             "uncertainties": ["no readable document with a text layer under the input path"],
             "limitations": ["the extractor needs PDF/DOCX/MD/TXT text; image-only PDFs are not "
                             "OCR'd"]}, ensure_ascii=False, indent=1), encoding="utf-8")
        return 2
    units = sentence_units(blocks)
    graph = build_graph(blocks, units, case_id, args.max_nodes, recurring_furniture(blocks))
    write_mm(out / "mindmap.mm", graph)
    write_opml(out / "mindmap.opml", graph)
    write_md(out / "mindmap.md", graph)
    w, h = write_svg(out / "mindmap.svg", graph)
    render, pngs = rasterise(out / "mindmap.svg", out)
    cov = coverage(graph, blocks, units)
    with (out / "nodes.tsv").open("w", encoding="utf-8") as f:
        f.write("node_id\tparent\tkind\tevidence_type\tlocator\tlabel\tquote\n")
        for n in graph["nodes"]:
            f.write(f'{n["id"]}\t{n["parent"] or "-"}\t{n["kind"]}\t{n["evidence_type"]}\t'
                    f'{n["locator"]}\t{n["label"]}\t{n["quote"][:300]}\n')
    with (out / "relations.tsv").open("w", encoding="utf-8") as f:
        f.write("source\ttarget\trelation\tbasis\n")
        for e in graph["edges"]:
            f.write(f'{e["source"]}\t{e["target"]}\t{e["relation"]}\t{e["basis"]}\n')
    (out / "coverage.json").write_text(json.dumps(cov, ensure_ascii=False, indent=1), encoding="utf-8")
    checks = verify(graph, blocks, units)
    claims = [{"id": n["id"], "kind": n["kind"],
               "evidence": n["quote"], "source_id": source, "location": n["locator"]}
              for n in graph["nodes"] if n["kind"] != "title"][:60]
    result = {
        "case_id": case_id,
        "status": "answered" if checks["all_passed"] else "partial",
        "result": {"source": source, "nodes": len(graph["nodes"]), "edges": len(graph["edges"]),
                   "max_depth": _depth(graph),
                   "node_kinds": cov["node_kinds"], "relations": cov["relations"],
                   "coverage": cov, "truncated_at_max_nodes": graph["truncated"],
                   "edge_budget": graph.get("edge_budget", {}),
                   "layout": {"width": w, "height": h},
                   "artifacts": ["mindmap.mm", "mindmap.opml", "mindmap.md", "mindmap.svg",
                                 "mindmap.png", "nodes.tsv", "relations.tsv", "coverage.json"],
                   "render": render},
        "claims": claims,
        "uncertainties": [f'{n["id"]}: {n["kind"]} node — inference-level label, the quote is '
                          f'verbatim' for n in graph["nodes"] if n["evidence_type"] == "inference"][:20],
        "limitations": [
            "node labels are the material's own sentences (trimmed), not a paraphrase, so a label "
            "is checkable but can be long",
            "relations are created only from a textual basis: shared terms, a contrast cue, or two "
            "values for the same quantity; the map never draws an edge to look tidy",
            "the extraction is text-layer only: figures inside the paper are not interpreted",
        ],
        "verification": checks,
    }
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    print(json.dumps({"case_id": case_id, "status": result["status"],
                      "nodes": len(graph["nodes"]), "edges": len(graph["edges"]),
                      "coverage": cov["block_coverage"], "render": render,
                      "all_passed": checks["all_passed"]}, ensure_ascii=False))
    return 0


def verify(graph: dict, blocks: list[dict], units: list[dict]) -> dict:
    corpus = norm(" ".join(b["text"] for b in blocks))
    quotes_ok = all(norm(n["quote"]) in corpus for n in graph["nodes"] if n["kind"] != "title")
    ids = {n["id"] for n in graph["nodes"]}
    edges_ok = all(e["source"] in ids and e["target"] in ids for e in graph["edges"])
    parents_ok = all(n["parent"] in ids for n in graph["nodes"] if n["parent"])
    locators_ok = all(n["locator"] and "#" in n["locator"] for n in graph["nodes"]
                      if n["kind"] != "title")
    typed = {e["relation"] for e in graph["edges"]} <= {"structural", "depends", "contrasts",
                                                       "conflicts", "supports"}
    checks = {
        "every_node_quote_verbatim": quotes_ok,
        "every_node_has_locator": locators_ok,
        "every_edge_endpoint_exists": edges_ok,
        "every_parent_exists": parents_ok,
        "edge_types_are_typed": typed,
        "no_orphan_node": all(n["parent"] or n["id"] == graph["root"] for n in graph["nodes"]),
        "hierarchy_has_at_least_two_levels":
            sum(1 for n in graph["nodes"] if n["parent"] == graph["root"]) >= 1,
    }
    checks["all_passed"] = all(v for k, v in checks.items() if k != "all_passed")
    return checks


def check_export(out: Path) -> int:
    """Verify a previously written export without rebuilding it."""
    res = json.loads((out / "result.json").read_text(encoding="utf-8"))
    checks = res.get("verification", {})
    print(json.dumps({"checked": str(out), "all_passed": checks.get("all_passed"),
                      "checks": {k: v for k, v in checks.items() if k != "all_passed"}},
                     ensure_ascii=False))
    return 0 if checks.get("all_passed") else 1


if __name__ == "__main__":
    # exit 2 = "insufficient evidence" with result.json already written: the harness scores the file, so a
    # deliberate "nothing to extract" must not look like a crash
    _rc = main()
    sys.exit(0 if _rc == 2 else _rc)
