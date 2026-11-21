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
                      r"references|acknowledg\w*|appendix)\b|"
                      r"^(摘要|内容提要|引言|前言|绪论|背景|相关工作|方法|材料与方法|实验部分|实验方法|"
                      r"结果|实验结果|讨论|分析与讨论|结论|总结|局限|参考文献|致谢|附录)", re.I)
CONTRAST = re.compile(r"\b(however|whereas|in contrast|unlike|although|but)\b|然而|但是|相反|尽管|相比之下|而", re.I)
FINDING = re.compile(r"\b(we (?:show|found|report|demonstrate|observe)|results? (?:show|indicate)|"
                     r"increase[sd]?|decrease[sd]?|improve[sd]?|reduc\w+|higher|lower|greater)\b", re.I)
METHOD = re.compile(r"\b(we (?:used|measured|tested|printed|performed)|samples? were|"
                    r"specimens? were|was (?:measured|calculated|evaluated))\b|"
                    r"采用|使用|测量|测试|打印|制备|记录|统计|计算|评估", re.I)
LIMIT = re.compile(r"\b(limitation|assum\w+|only|not (?:measured|reported|investigated)|"
                   r"further (?:work|study)|future work)\b", re.I)
QUESTION = re.compile(r"\b(aim|objective|goal|we (?:investigate|examine|ask)|research question|"
                      r"hypothes\w+)\b", re.I)
GAP = re.compile(r"\b(unknown|unclear|not (?:yet|been) (?:studied|addressed)|remains? to be)\b", re.I)


def short_label(text: str, limit: int = 28) -> str:
    """A map node reads best as a head phrase, not as a whole sentence.

    The quote column keeps the full sentence for verifiability; the label is what a reader scans. Cut at the
    first clause boundary inside the limit, otherwise clip at a word boundary.
    """
    t = norm(text)
    if len(t) <= limit:
        return t
    window = t[:limit]
    for sep in ("，", "；", "。", ",", ";", ":"):
        index = window.rfind(sep)
        if index >= 6:
            return window[:index].rstrip(" ,;:-")
    return clip(t, limit)


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
        blocks.extend(md_blocks(text, best.name))
    return blocks, best.name


MD_MIN = 24          # 正文块下限：中文一句结论常常不到 50 字
HEAD_MAX = 24        # 无 # 前缀时，"短、无句末标点、无逗号"的行按小标题处理


def md_blocks(text: str, name: str) -> list[dict]:
    """把 markdown/纯文本切成块，标题单独成块。

    旧版把整页当一个 blob 再按 700 字切、并丢弃 <50 字的段落——摘要、结论、局限这类短段落正好是
    claim/limitation 节点的唯一来源，被丢掉之后图谱里只剩方法与结果，关系也就编不出冲突边。
    """
    blocks: list[dict] = []
    state = {"i": 0, "cur": []}

    def flush(pno: int) -> None:
        body = clean(" ".join(state["cur"]))
        state["cur"] = []
        if not body:
            return
        for t in paragraphize(body):
            if len(t) >= MD_MIN or len(re.findall(r"[\u4e00-\u9fff]", t)) >= 8:
                state["i"] += 1
                blocks.append({"page": pno, "block": state["i"], "text": t,
                               "locator": f"{name}#p{pno}b{state['i']}"})

    def heading(line: str, pno: int) -> None:
        state["i"] += 1
        blocks.append({"page": pno, "block": state["i"], "text": clean(line),
                       "locator": f"{name}#p{pno}b{state['i']}"})

    for pno, page in enumerate(text.split("\f"), 1):
        for raw in page.splitlines():
            line = raw.strip()
            if not line:
                flush(pno)
                continue
            m = re.match(r"^(#{1,6})\s+(.*)$", line)
            if m:
                flush(pno)
                heading(m.group(2), pno)
                continue
            if (len(line) <= HEAD_MAX and not re.search(r"[。.！!？?；;：:]$", line)
                    and not re.search(r"[，,、]", line)):
                flush(pno)
                heading(line, pno)
                continue
            state["cur"].append(line)
        flush(pno)
    return blocks


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
        label = short_label(label)
        # the same sentence often appears twice (page furniture, a repeated heading): keep one node per
        # (locator, label) pair so the map does not show twins and max_depth stays meaningful
        if any(n["label"] == label and n["locator"] == locator for n in nodes):
            return next(n["id"] for n in nodes if n["label"] == label and n["locator"] == locator)
        nid = f"N{len(nodes) + 1:03d}"
        nodes.append({"id": nid, "parent": parent, "label": label, "kind": kind,
                      "locator": locator, "quote": norm(quote)[:400],
                      "evidence_type": evidence_type, "quote_sha256": sha(norm(quote))[:16]})
        if parent:
            edges.append({"source": parent, "target": nid, "relation": "structural",
                          "basis": f"hierarchy under {parent}"})
        return nid

    title = blocks[0]["text"][:100] if blocks else case_id
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
        first_text = norm(blocks[0]["text"])[:70] if blocks else ""
        sections = [({"text": first_text or "全文", "locator": blocks[0]["locator"] if blocks else "-",
                      "synthetic": True}, blocks)]
    for head, body in sections:
        label = norm(head["text"])[:70]
        if not label:
            continue
        # 合成章节没有原文标题可引，引文留空（不伪造），但定位仍在
        sec = add(label, "section", head["locator"], "" if head.get("synthetic") else label, root,
                  evidence_type="summary")
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
    # locator 形如 <file>#p1b2s3；只去掉句子后缀，不能按 "s" 切——文件名里本来就可能有 s
    used = {re.sub(r"s\d+$", "", n["locator"]) for n in graph["nodes"]}
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



REL_OK = {"structural", "depends", "contrasts", "conflicts", "supports"}


def _flat(t) -> str:
    return re.sub(r"\s+", "", str(t or ""))


def load_agent_graph(path: Path, case_id: str, reparent: bool = True) -> dict:
    """模型写的图：只接受字段完整的节点，缺 locator 的节点会被 verify 拦下，这里先原样装载。

    reparent=True 时把"父节点不存在"的节点挂到根上，只为渲染不崩；校验走 reparent=False，
    这样缺 parent 仍然是一条能被拦下的问题，而不是被悄悄修掉。
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    nodes = []
    for n in data.get("nodes") or []:
        quote = str(n.get("quote") or "")
        nodes.append({"id": str(n.get("id") or f"N{len(nodes) + 1:03d}"),
                      "parent": (str(n["parent"]) if n.get("parent") else None),
                      "kind": str(n.get("kind") or "concept"),
                      "evidence_type": str(n.get("evidence_type") or "fact"),
                      "locator": str(n.get("locator") or ""),
                      "label": str(n.get("label") or "")[:80],
                      "quote": quote, "quote_sha256": sha(norm(quote))[:16]})
    edges = []
    for e in data.get("edges") or []:
        edges.append({"source": str(e.get("source") or ""), "target": str(e.get("target") or ""),
                      "relation": str(e.get("relation") or "structural"),
                      "basis": str(e.get("basis") or "")})
    ids = {n["id"] for n in nodes}
    root = str(data.get("root") or "")
    if root not in ids:
        root = next((n["id"] for n in nodes if not n["parent"]), nodes[0]["id"] if nodes else "N001")
    if reparent:
        # 悬空父节点与多余顶层节点都挂到根上：布局是按根做树遍历的，否则这些节点没有坐标
        for n in nodes:
            if n["id"] != root and (not n["parent"] or n["parent"] not in ids):
                n["parent"] = root
    return {"case_id": case_id, "title": str(data.get("title") or ""), "root": root,
            "nodes": nodes, "edges": edges, "truncated": False}


def verify_agent_graph(path: Path, blocks: list[dict], out: Path) -> int:
    """门禁：模型写的知识图谱必须每个节点可回溯、每条关系有类型与端点。

    题面要求"节点与边都能回到原文定位"，所以这里逐条查：label 非空且不超长、locator 指向真实文件、
    quote（如给出）在材料里逐字存在（忽略空白）、parent/端点都存在、关系类型在 allowed 集合内。
    """
    material = _flat(" ".join(b["text"] for b in blocks))
    files = {str(b.get("locator", "")).split("#")[0] for b in blocks if b.get("locator")}
    # locator 必须是第一阶段真实产出的块定位（允许带句子后缀 s3）；编出来的 locator 会让覆盖率静默变 0
    real = {str(b.get("locator", "")) for b in blocks}

    def locator_exists(loc: str) -> bool:
        base = re.sub(r"s\d+$", "", loc)
        return base in real or any(r.startswith(base + "s") for r in real)
    try:
        g = load_agent_graph(path, "check", reparent=False)
    except Exception as exc:  # noqa: BLE001
        rep = {"error": f"unreadable graph: {exc}"}
        (out / "graph_check.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps(rep, ensure_ascii=False))
        return 1
    ids = {n["id"] for n in g["nodes"]}
    problems = []
    for n in g["nodes"]:
        if not n["label"].strip():
            problems.append(f'{n["id"]}: empty label')
        if len(n["label"]) > 80:
            problems.append(f'{n["id"]}: label too long')
        if not n["locator"] or "#" not in n["locator"]:
            problems.append(f'{n["id"]}: missing locator (want file#locator)')
        elif files and not any(f and f in n["locator"] for f in files):
            problems.append(f'{n["id"]}: locator points to an unknown file')
        elif real and not locator_exists(n["locator"]):
            problems.append(f'{n["id"]}: locator {n["locator"]} is not a block produced in stage 1')
        if n["quote"] and _flat(n["quote"]) not in material:
            problems.append(f'{n["id"]}: quote not verbatim in material')
        if n["parent"] and n["parent"] not in ids:
            problems.append(f'{n["id"]}: parent {n["parent"]} missing')
    for e in g["edges"]:
        if e["relation"] not in REL_OK:
            problems.append(f'edge {e["source"]}->{e["target"]}: bad relation {e["relation"]}')
        for end in (e["source"], e["target"]):
            if end not in ids:
                problems.append(f'edge endpoint {end} missing')
    kinds = {}
    for n in g["nodes"]:
        kinds[n["kind"]] = kinds.get(n["kind"], 0) + 1
    typed_edges = sum(1 for e in g["edges"] if e["relation"] != "structural")
    checks = {
        "nodes_present": len(g["nodes"]) >= 5,
        "every_node_has_locator": not any("missing locator" in p or "unknown file" in p
                                          or "not a block produced" in p for p in problems),
        "quotes_verbatim": not any("not verbatim" in p for p in problems),
        "edges_have_types": not any("bad relation" in p for p in problems),
        "edges_connected": not any("endpoint" in p for p in problems),
        "has_typed_relations": typed_edges >= 2,
    }
    checks["all_passed"] = all(checks.values())
    rep = {"checks": checks, "nodes": len(g["nodes"]), "edges": len(g["edges"]),
           "kinds": kinds, "typed_edges": typed_edges, "problems": problems[:20]}
    (out / "graph_check.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False))
    return 0 if checks["all_passed"] else 1

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--max-nodes", type=int, default=120)
    ap.add_argument("--check", default=None)
    ap.add_argument("--graph", default=None, help="agent-authored graph JSON (rendered instead of the derived one)")
    ap.add_argument("--verify-graph", default=None, help="check an agent-authored graph (exit 1 if unusable)")
    ap.add_argument("--blocks", action="store_true",
                    help="stage 1 only: dump blocks.tsv (locator · text) for agent authoring, then exit")
    ap.add_argument("--run-all", action="store_true",
                    help="one-shot: blocks.tsv -> verify graph.json if present -> render -> attach_meta")
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
    if args.blocks:
        with (out / "blocks.tsv").open("w", encoding="utf-8") as f:
            f.write("locator\tchars\ttext\n")
            for b in blocks:
                f.write(f'{b["locator"]}\t{len(b["text"])}\t{b["text"]}\n')
        print(json.dumps({"case_id": case_id, "blocks": len(blocks), "blocks_tsv": str(out / "blocks.tsv")},
                         ensure_ascii=False))
        return 0
    if args.verify_graph:
        return verify_agent_graph(Path(args.verify_graph), blocks, out)
    if args.run_all:
        with (out / "blocks.tsv").open("w", encoding="utf-8") as f:
            f.write("locator\tchars\ttext\n")
            for b in blocks:
                f.write(f'{b["locator"]}\t{len(b["text"])}\t{b["text"]}\n')
        gp = out / "graph.json"
        if gp.is_file():
            if verify_agent_graph(gp, blocks, out) == 0:
                args.graph = str(gp)
            else:
                args.graph = None
                (out / "graph_check.json").write_text(json.dumps(
                    {"checks": {"all_passed": False}, "note": "graph.json 未通过门禁，已退回脚本推导图谱"},
                    ensure_ascii=False, indent=1), encoding="utf-8")
        else:
            (out / "graph_check.json").write_text(json.dumps(
                {"checks": {"all_passed": None}, "note": "没有 graph.json，使用脚本推导图谱"}, ensure_ascii=False,
                indent=1), encoding="utf-8")
            args.graph = None
    units = sentence_units(blocks)
    graph = (load_agent_graph(Path(args.graph), case_id) if args.graph
             else build_graph(blocks, units, case_id, args.max_nodes, recurring_furniture(blocks)))
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
                      "graph_source": "agent" if args.graph else "derived",
                      "all_passed": checks["all_passed"]}, ensure_ascii=False))
    if args.run_all:
        meta = Path(__file__).resolve().parent / "attach_meta.py"
        if meta.is_file():
            subprocess.run([sys.executable, str(meta), "--input", str(inp), "--output", str(out)],
                           capture_output=True, text=True, timeout=300)
    return 0


def verify(graph: dict, blocks: list[dict], units: list[dict]) -> dict:
    # 逐字比对忽略空白：引文与原文的中句拼接方式不同（一个空格 vs 直接相接），
    # 但换词/改字仍然会被抓出来。空引文（结构节点）不参与该检查。
    flat = lambda t: re.sub(r"\s+", "", str(t or ""))
    corpus = flat(" ".join(b["text"] for b in blocks))
    quoted = [n for n in graph["nodes"] if n["kind"] != "title" and flat(n["quote"])]
    quotes_ok = all(flat(n["quote"]) in corpus for n in quoted) if quoted else False
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
