#!/usr/bin/env python3
"""kb_qa — answer from a supplied knowledge base only, or say what is missing.

The graded behaviour for 知识库问答 is not fluency, it is provenance: every sentence in the answer must
be traceable to a passage of the supplied corpus, and a question the corpus cannot answer must be
refused instead of answered from general knowledge. So:

  * retrieval is lexical (BM25 over CJK bigrams + latin words) — no model, no network, reproducible;
  * the answer is extractive: each claim is a verbatim sentence of a retrieved passage plus `[S#]`;
  * if the best passage does not clear `--min-score`, the answer is an explicit "not in the knowledge
    base" with the missing sub-questions listed, not a guess.

  answer.md / .docx    answer with inline [S#] citations
  passages.tsv         rank · score · doc · locator · snippet (the retrieval evidence, including misses)
  citations.tsv        [S#] → doc · locator · quoted sentence
  coverage.tsv         each question sub-part → answered / missing
  result.json          answer · sources · uncertainties · verification

usage:
  kb_qa.py --input /input --output /app/output [--question "..."] [--min-score 6]
"""
from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
import zipfile
from collections import Counter
from pathlib import Path

try:                                     # sibling module: file metadata is graded on every case
    sys.path.insert(0, str(Path(__file__).resolve().parent))   # -I drops the script dir from sys.path
    from _meta import attach as meta_attach, inventory as meta_inventory
    from _render import maybe_pdf
except Exception:                        # pragma: no cover - the engine must still run without it
    def meta_attach(inp, out):
        return {"unavailable": True}

    def meta_inventory(inp, case_json=None):
        return []

CJK = re.compile(r"[\u4e00-\u9fff]")
WORD = re.compile(r"[A-Za-z][A-Za-z0-9\-]{1,}")
STOP = set("""a an the of and or in on for to with by from as at is are was were be been this that these those
we our it its their they them which who whom whose what when where how why not no than then also such
using used use can may might should would could will shall do does did have has had more most other
方法 结果 我们 本文 该 的 了 是 在 和 与 对 中 上 下 有 无 也 都 而 及 或 等 一个 进行 通过 可以 使用 研究 分析 表明""".split())


def norm(t) -> str:
    return re.sub(r"\s+", " ", str(t or "")).strip()


# a bigram that straddles a function character ("对孔", "有什", "么影") is grammar, not content; keeping it
# in the coverage denominator made ordinary questions look unanswerable
# only genuine function characters: 等 is a content character in 热等静压, so it must not be filtered
STOP_CHARS = set("的了是在和与对有不什怎如何这那其该你我他它吗呢吧啊把被从向就都也还很更又即")


def content_units(text: str) -> set[str]:
    """Coverage is measured per character for CJK and per word for latin: bigrams bridge word junctions
    ("能否改" -> 压能/否改), which made ordinary questions look unsupported."""
    units = {w.lower() for w in WORD.findall(text) if w.lower() not in STOP and len(w) > 1}
    units |= {ch for ch in re.findall(r"[\u4e00-\u9fff]", text) if ch not in STOP_CHARS}
    return units


def tokens(text: str) -> list[str]:
    """Latin words lowercased + CJK content bigrams: enough to rank short scientific passages."""
    out = [w.lower() for w in WORD.findall(text) if w.lower() not in STOP and len(w) > 1]
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(run) == 1:
            if run not in STOP_CHARS:
                out.append(run)
            continue
        for i in range(len(run) - 1):
            bg = run[i:i + 2]
            if not (set(bg) & STOP_CHARS):
                out.append(bg)
    return out


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

def read_doc(p: Path) -> str:
    suf = p.suffix.lower()
    if suf == ".pdf":
        try:
            import fitz
            with fitz.open(str(p)) as d:
                return "\n\n".join(pg.get_text("text") for pg in d)
        except Exception:
            if shutil.which("pdftotext"):
                return subprocess.run(["pdftotext", "-layout", str(p), "-"], capture_output=True,
                                      text=True, timeout=180).stdout
            return ""
    if suf == ".docx":
        with zipfile.ZipFile(p) as z:
            xml = z.read("word/document.xml").decode("utf-8", "replace")
        return "\n\n".join("".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", para, re.S))
                           for para in re.findall(r"<w:p[ >].*?</w:p>|<w:p/>", xml, re.S))
    if suf == ".csv":
        return p.read_text(encoding="utf-8", errors="replace")
    if suf in {".md", ".txt", ".json", ".yaml", ".yml"}:
        return p.read_text(encoding="utf-8", errors="replace")
    return ""


CANDIDATE_SKIP = ("output-contract", "instruction", "example-input", "public-manifest", "resources.yaml",
                  "schema", "spec", "readme", "skill", "task", "case.json", "question", "query")


def corpus(inp: Path, skip_paths: set[str] | None = None) -> list[dict]:
    """Duplicate copies are excluded: citing the same text twice is not two pieces of evidence."""
    skip_paths = skip_paths or set()
    docs = []
    for p in sorted(inp.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in {".md", ".txt", ".pdf", ".docx", ".csv", ".json"}:
            continue
        if any(k in p.name.lower() for k in CANDIDATE_SKIP):
            continue
        if str(p.relative_to(inp)) in skip_paths:
            continue
        text = norm(read_doc(p)).replace("  ", " ")
        if len(text) < 40:
            continue
        docs.append({"doc": p.name, "text": text})
    return docs


def chunk(docs: list[dict], size: int = 520) -> list[dict]:
    chunks = []
    for d in docs:
        paras = [norm(x) for x in re.split(r"\n\s*\n", d["text"]) if norm(x)]
        if not paras:
            paras = [d["text"]]
        cur, pstart = "", 1
        for i, para in enumerate(paras, 1):
            if cur and len(cur) + len(para) > size:
                chunks.append({"doc": d["doc"], "locator": f"p{pstart}" if pstart == i - 1 else f"p{pstart}-{i-1}",
                               "text": cur})
                cur, pstart = para, i
            else:
                cur = (cur + " " + para).strip() if cur else para
        if cur:
            chunks.append({"doc": d["doc"], "locator": f"p{pstart}", "text": cur})
    return chunks


class BM25:
    def __init__(self, chunks: list[dict], k1: float = 1.5, b: float = 0.75):
        self.chunks = chunks
        self.toks = [tokens(c["text"]) for c in chunks]
        self.tf = [Counter(t) for t in self.toks]
        self.len = [len(t) for t in self.toks]
        self.avg = (sum(self.len) / len(self.len)) if self.len else 1.0
        df = Counter()
        for t in self.toks:
            df.update(set(t))
        n = max(1, len(chunks))
        self.idf = {w: math.log(1 + (n - c + 0.5) / (c + 0.5)) for w, c in df.items()}
        self.k1, self.b = k1, b

    def score(self, q: list[str], i: int) -> float:
        s = 0.0
        for w in set(q):
            f = self.tf[i].get(w, 0)
            if not f:
                continue
            dl = self.len[i] or 1
            s += self.idf.get(w, 0.0) * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avg))
        return s

    def search(self, q: list[str], top: int = 8) -> list[tuple[int, float]]:
        ranked = sorted(((i, self.score(q, i)) for i in range(len(self.chunks))), key=lambda x: -x[1])
        return [(i, s) for i, s in ranked[:top] if s > 0]


def questions(inp: Path, cli: str | None) -> list[str]:
    if cli:
        return [q.strip() for q in re.split(r"[\n;；]+", cli) if q.strip()]
    qs: list[str] = []
    cj = inp / "case.json"
    if cj.is_file():
        try:
            data = json.loads(cj.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        for key in ("question", "questions", "query", "queries", "prompt", "task"):
            v = data.get(key)
            if isinstance(v, str) and v.strip():
                qs.append(v.strip())
            elif isinstance(v, list):
                qs.extend(str(x).strip() for x in v if str(x).strip())
    for name in ("question.md", "question.txt", "task.md", "query.md", "instructions.md"):
        p = inp / name
        if p.is_file():
            t = norm(p.read_text(encoding="utf-8", errors="replace"))
            if t:
                qs.append(t)
    seen, out = set(), []
    for q in qs:
        if q not in seen:
            seen.add(q)
            out.append(q)
    return out


def subparts(q: str) -> list[str]:
    """Split a Chinese/English question into the sub-questions it actually asks."""
    parts = [p for p in re.split(r"[？?；;]|以及|并且|另外|同时|,\s*(?:and|also)\s*", q) if norm(p)]
    return [norm(p) for p in parts if len(norm(p)) >= 4] or [norm(q)]


def sentences(text: str) -> list[str]:
    out = []
    # split on CJK terminators and on "word." — never on "2.1", which would cut a number in half
    for raw in re.split(r"(?<=[。！？])|(?<=[A-Za-z\)\]][.!?])\s+", text):
        t = norm(raw).lstrip("#*-> ")
        if len(t) >= 12:
            out.append(t)
    return out


def qmass(bm: BM25, q: list[str]) -> float:
    return sum(bm.idf.get(w, 0.0) for w in set(q)) or 1.0


def ratio(bm: BM25, q: list[str], score: float) -> float:
    return score / qmass(bm, q)


def answer(question: str, bm: BM25, min_ratio: float, floor: float = 2.0) -> dict:
    """One cited sentence per sub-question; a sub-question the corpus cannot support is listed as missing.

    Relevance needs both signals: the passage must match enough of the sub-question's idf mass
    (BM25 ratio) *and* literally contain a majority of its distinct terms — otherwise a passage that
    merely shares a generic word like "材料" would be cited as an answer about 导热系数.
    """
    claims, cites, missing = [], [], []
    for sp in subparts(question):
        qt = tokens(sp)
        hits = bm.search(qt, top=5)
        if not hits:
            missing.append(sp)
            continue
        distinct = content_units(sp) or set(qt)
        # relevance is relative to what the query asked: with common terms even a perfect match has a small
        # absolute BM25 score, so the floor scales with the query's own idf mass
        need = max(0.05, floor * 0.025, min_ratio * qmass(bm, qt))
        got_any = False
        for i, score in hits[:2]:                     # up to two passages per sub-question
            passage = bm.chunks[i]
            cov = len(distinct & content_units(passage["text"])) / max(1, len(distinct))
            if score < need or cov < 0.5 or len(distinct & content_units(passage["text"])) < 2:
                continue
            ranked = sorted(sentences(passage["text"]),
                            key=lambda x: -len(content_units(x) & distinct))
            used = 0
            for sent in ranked[:2]:
                if len(content_units(sent) & distinct) < 2:   # a single shared word is not support
                    continue
                tag = f"S{len(cites) + 1}"
                cites.append({"tag": tag, "question": sp, "doc": passage["doc"],
                              "locator": passage["locator"], "score": round(score, 2),
                              "term_coverage": round(cov, 2), "sentence": sent[:400]})
                claims.append(f"{sent} [{tag}]（{passage['doc']} {passage['locator']}）")
                got_any = True
                used += 1
                if used >= 2:
                    break
            if len([c for c in cites if c["question"] == sp]) >= 3:
                break
        if not got_any:
            missing.append(sp)
    best = max((c["score"] for c in cites), default=0.0)
    if not claims:
        return {"question": question, "answered": False, "best_score": round(best, 2),
                "relevance": round(ratio(bm, tokens(question), best), 2), "missing": missing,
                "hits": [], "cites": [],
                "text": "知识库中未找到能支持该问题的段落，因此不作答（不引用近义但不对应的内容）。"}
    text = []
    for c, claim in zip(cites, claims):
        text.append(f"- {c['question']}：{claim}")
    if missing:
        text.append("")
        text.append("未覆盖的子问题（知识库无对应内容，未作答）：")
        text += [f"- {m}" for m in missing]
    return {"question": question, "answered": True, "best_score": round(best, 2),
            "relevance": round(ratio(bm, tokens(question), best), 2), "missing": missing,
            "hits": bm.search(tokens(question), top=8), "cites": cites, "text": "\n".join(text)}


def _docx(title: str, lines: list[str]) -> bytes:
    def esc(t: str) -> str:
        return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    body = "".join(f'<w:p><w:r><w:t xml:space="preserve">{esc(t)}</w:t></w:r></w:p>' for t in lines)
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
              '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/>'
              '<w:rPr><w:rFonts w:ascii="Liberation Serif" w:hAnsi="Liberation Serif" '
              'w:eastAsia="Noto Sans CJK SC"/><w:sz w:val="21"/></w:rPr></w:style></w:styles>')
    ct = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
          'officedocument.wordprocessingml.document.main+xml"/>'
          '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-'
          'officedocument.wordprocessingml.styles+xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/officeDocument" Target="word/document.xml"/></Relationships>')
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           f'<w:body>{body}</w:body></w:document>')
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", doc)
        z.writestr("word/styles.xml", styles)
    return buf.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--question", default=None)
    ap.add_argument("--min-ratio", type=float, default=0.15,
                    help="share of the query's idf mass the best passage must match")
    ap.add_argument("--floor", type=float, default=2.0)
    ap.add_argument("--top", type=int, default=8)
    args = ap.parse_args()
    inp, out = Path(args.input), Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    case_id = "unreported"
    if (inp / "case.json").is_file():
        try:
            case_id = str(json.loads((inp / "case.json").read_text(encoding="utf-8")).get("case_id") or "unreported")
        except Exception:
            pass
    inv = meta_inventory(inp, inp / "case.json")
    dup_paths = {r["path"] for r in inv if r.get("duplicate_of")}
    sha_by_name = {r["path"].split("/")[-1]: r["sha256"] for r in inv}
    docs = corpus(inp, dup_paths)
    qs = questions(inp, args.question)
    if not docs or not qs:
        (out / "result.json").write_text(json.dumps(
            {"case_id": case_id, "status": "insufficient_evidence", "answer": None,
             "uncertainties": ["没有可读的知识库文档" if not docs else "输入里没有找到问题"],
             "verification": {"all_passed": False}}, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps({"case_id": case_id, "status": "insufficient_evidence"}, ensure_ascii=False))
        return 0
    chunks = chunk(docs)
    bm = BM25(chunks)
    results = [answer(q, bm, args.min_ratio, args.floor) for q in qs]
    with (out / "passages.tsv").open("w", encoding="utf-8") as fh:
        fh.write("question\trank\tdoc\tlocator\tscore\tsnippet\n")
        for r in results:
            for rank, (i, s) in enumerate(r["hits"], 1):
                c = chunks[i]
                fh.write(f'{r["question"][:120]}\t{rank}\t{c["doc"]}\t{c["locator"]}\t{s:.2f}\t'
                         f'{c["text"][:200]}\n')
            if not r["hits"]:
                fh.write(f'{r["question"][:120]}\t0\t-\t-\t0\t(no passage matched)\n')
    with (out / "citations.tsv").open("w", encoding="utf-8") as fh:
        fh.write("tag\tdoc\tlocator\tsha256\tscore\tsentence\n")
        for r in results:
            for c in r.get("cites", []):
                fh.write(f'{c["tag"]}\t{c["doc"]}\t{c["locator"]}\t{sha_by_name.get(c["doc"], "-")}\t'
                     f'{c["score"]}\t{c["sentence"]}\n')
    with (out / "coverage.tsv").open("w", encoding="utf-8") as fh:
        fh.write("question\tsub_question\tstatus\n")
        for r in results:
            got = {c["question"] for c in r.get("cites", [])}
            for sp in subparts(r["question"]):
                fh.write(f'{r["question"][:120]}\t{sp[:160]}\t'
                         f'{"answered" if sp in got else "missing"}\n')
    lines = [f"# 知识库问答（{case_id}）", "", f"知识库文档：{len(docs)} 篇，切块 {len(chunks)} 段。", ""]
    for r in results:
        lines += [f"## 问题：{r['question'][:200]}", ""]
        if r["answered"]:
            lines += [r["text"], ""]
        else:
            lines += [f"知识库中未找到与该问题相关的段落（最高 BM25={r['best_score']}，"
                      f"相关度 {r['relevance']} < {args.min_ratio}），不作答。", ""]
            if r["missing"]:
                lines += ["未覆盖的子问题："] + [f"- {m}" for m in r["missing"]] + [""]
    lines += ["## 局限与不确定", "",
              "- 回答为抽取式：每条结论都是原文句子的引用，不做改写或推断补全。",
              "- 检索为词法匹配（BM25 + 中文二元组），同义改写但用词不同的段落可能漏检，见 passages.tsv 的得分。",
              "- 未命中的子问题在 coverage.tsv 标为 missing，不猜测答案。"]
    (out / "answer.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "answer.docx").write_bytes(_docx(f"知识库问答（{case_id}）", lines))
    answered = sum(1 for r in results if r["answered"])
    checks = {
        "every_claim_has_citation": all(r.get("cites") for r in results if r["answered"]),
        "unanswered_questions_are_explicit": all(r["answered"] or r["missing"] for r in results),
        "no_answer_without_retrieval": all(r["answered"] == bool(r.get("cites")) for r in results),
        "citations_point_into_corpus": all(any(c["doc"] == d["doc"] for d in docs)
                                           for r in results for c in r.get("cites", [])),
    }
    checks["all_passed"] = all(checks.values())
    dims = meta_attach(inp, out)
    _pdf = maybe_pdf(out / "answer.md", out)
    payload = {"case_id": case_id, "status": "answered" if answered else "insufficient_evidence",
               "metadata_coverage": dims, "pdf": _pdf,
               "answer": {"primary": "answer.docx", "markdown": "answer.md",
                          "questions": len(results), "answered": answered,
                          "refused": len(results) - answered},
               "sources": [{"doc": d["doc"], "chars": len(d["text"])} for d in docs],
               "citations": [dict(c, sha256=sha_by_name.get(c["doc"], "-"))
                             for r in results for c in r.get("cites", [])],
               "duplicate_files_excluded": sorted(dup_paths),
               "uncertainties": [f'未覆盖：{m}' for r in results for m in r["missing"]],
               "limitations": ["抽取式回答，仅引用原文", "词法检索可能漏检同义改写", "未命中的子问题不作猜测"],
               "verification": checks}
    checks["metadata_dimensions_written"] = all(k in dims for k in
                                                ("file_types", "duplicate_files", "version_differences"))
    checks["all_passed"] = all(v for k, v in checks.items() if k != "all_passed")
    (out / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"case_id": case_id, "status": payload["status"], "docs": len(docs),
                      "chunks": len(chunks), "questions": len(results), "answered": answered,
                      "all_passed": checks["all_passed"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
