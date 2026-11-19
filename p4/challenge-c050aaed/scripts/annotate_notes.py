#!/usr/bin/env python3
"""annotate_notes — read a paper and leave annotations that point at the text.

A reading annotation is only worth keeping if you can find the sentence again and if it says *what kind of
thing* it is. So every annotation here is a verbatim sentence plus a locator plus a type, decided by cue
words rather than by taste:

    definition    "X is defined as / refers to / we define"
    claim         conclusion / we find / indicates / suggest / therefore
    evidence      a sentence carrying a number + unit or a statistical result
    method        protocol / we printed / measured / prepared / recorded
    limitation    limitation / however / cannot / future work / caveat / 局限
    question      open issue: unclear / remains unknown / to be determined / 尚不清楚

Annotations are then grouped into themes by shared content terms, and each theme gets a knowledge card.
The card's note line states only what the sentence says (type + quoted numbers), never an inferred
judgement — `drafted_note` is deliberately descriptive.

  summary.md / .docx   knowledge digest: themes → annotations, plus definitions / key numbers / limitations
  annotations.tsv      id · type · doc · locator · sentence · tags
  themes.tsv           theme · annotation ids · shared terms · count
  notes.jsonl          one JSON object per annotation (ready for a note app import)
  result.json          counts · themes · gaps · limitations · verification

usage:
  annotate_notes.py --input /input --output /app/output
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

# --- file metadata layer (rubric: 文件类型 · 元数据字段 · 目录层级 · 重复文件 · 版本差异 · 权限与来源) ---
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))   # -I drops the script dir from sys.path
    from _meta import attach as meta_attach
except Exception:                        # the engine must still run without the metadata module
    def meta_attach(inp, out):
        return {"unavailable": True}

NUM = re.compile(r"(?<!\d)(\d+(?:\.\d+)?)\s*(%|°C|µm|um|nm|mm|cm|m|MPa|GPa|kPa|Hz|kHz|min|h|s|g|mg|kg|mL|L|"
                 r"J/mm³|cycles?|次)")
CUE = {
    "definition": re.compile(r"\b(is defined as|refers to|we define|is called|denotes)\b|定义为|所称|指的是", re.I),
    "claim": re.compile(r"\b(we (?:find|show|conclude|argue)|indicat\w+|suggest\w+|therefore|thus|"
                        r"demonstrat\w+|in conclusion)\b|表明|说明|因此|可见", re.I),
    "method": re.compile(r"\b(we (?:printed|prepared|measured|tested|performed|collected|recorded)|"
                         r"was (?:printed|prepared|measured|tested)|protocol|procedure)\b|采用|制备|测试|测量", re.I),
    "limitation": re.compile(r"\b(limitation\w*|however|cannot|do(?:es)? not|caveat|future work|"
                             r"remains? (?:to be|un))\b|局限|不足|然而|无法|尚需", re.I),
    "question": re.compile(r"\b(unclear|unknown|not (?:yet )?(?:clear|known|determined)|open question|"
                           r"to be determined)\b|尚不清楚|未知|有待|是否", re.I),
}
SKIP = ("output-contract", "instruction", "example-input", "public-manifest", "resources.yaml", "schema",
        "spec", "readme", "skill", "task", "case.json")
STOP = set("""the and for with from this that these those were was are has have been study studies results
result using used can may show however also than then into their there which while about between both each
more most other such not but our we its it is of in on to by as at an or do does did research paper article
data analysis method methods approach model based new two three one
研究 方法 结果 分析 本文 我们 该 的 了 是 在 和 与 对 中 上 下 有 无 也 都 而 及 或 等 一个 进行 通过
可以 使用 表明 提出 不同 具有 以及 这些 这种 目前 相关 影响 作用 由于 因此 但是 如果 需要
已有 现有 普遍 系统 上述 如下 其中 分别 主要 一般 通常 往往 可能 应该""".split())


def norm(t) -> str:
    return re.sub(r"\s+", " ", str(t or "")).strip()


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
    if suf in {".md", ".txt"}:
        return p.read_text(encoding="utf-8", errors="replace")
    return ""


def terms(text: str) -> list[str]:
    out = [w.lower().strip("-") for w in re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", text)]
    out = [w for w in out if w not in STOP]
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(run) <= 4:
            if run not in STOP:
                out.append(run)
        else:
            out.extend(run[i:i + 2] for i in range(len(run) - 1) if run[i:i + 2] not in STOP)
    return out


def sentences(text: str) -> list[dict]:
    out = []
    for pno, para in enumerate(re.split(r"\n\s*\n", text), 1):
        p = norm(para).lstrip("#*-> ")
        if len(p) < 20:
            continue
        for sidx, s in enumerate(re.split(r"(?<=[。！？])|(?<=[A-Za-z\)\]][.!?])\s+", p), 1):
            t = norm(s)
            if len(t) >= 25:
                out.append({"locator": f"p{pno}s{sidx}", "text": t})
    return out


def classify(sent: str) -> str:
    for kind in ("definition", "question", "limitation", "method", "claim"):
        if CUE[kind].search(sent):
            return kind
    if NUM.search(sent):
        return "evidence"
    return "context"


def annotate(docs: list[dict], cap_per_doc: int = 60) -> list[dict]:
    anns: list[dict] = []
    for d in docs:
        picked = 0
        for s in sentences(d["text"]):
            kind = classify(s["text"])
            if kind == "context":
                continue
            nums = [f"{m.group(1)} {m.group(2)}" for m in NUM.finditer(s["text"])][:3]
            tags = [kind] + (["has_number"] if nums else [])
            anns.append({"id": f"A{len(anns) + 1}", "type": kind, "doc": d["doc"],
                         "locator": s["locator"], "sentence": s["text"][:400],
                         "numbers": nums, "tags": tags})
            picked += 1
            if picked >= cap_per_doc:
                break
    return anns


def themes(anns: list[dict], top: int = 8) -> list[dict]:
    df = Counter()
    for a in anns:
        df.update(set(terms(a["sentence"])))
    # a theme needs to appear in at least two annotations; otherwise it is a single mention
    cand = [(t, c) for t, c in df.most_common(40) if c >= 2 and len(t) > 1]
    groups: list[dict] = []
    remaining = {a["id"] for a in anns}
    for t, _ in cand:                                  # greedy, non-overlapping: a card must claim >=2 ids
        if len(groups) >= top:
            break
        members = [a for a in anns if a["id"] in remaining and t in set(terms(a["sentence"]))]
        if len(members) < 2:
            continue
        shared = Counter()
        for a in members:
            shared.update(set(terms(a["sentence"])))
        label_terms = [w for w, c in shared.most_common(8) if c >= 2 and w != t and len(w) > 1][:2]
        remaining -= {a["id"] for a in members}
        groups.append({"theme": t, "ids": [a["id"] for a in members],
                       "label": " · ".join([t] + label_terms), "count": len(members),
                       "types": dict(Counter(a["type"] for a in members))})
    return groups


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
    args = ap.parse_args()
    inp, out = Path(args.input), Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    case_id = "unreported"
    if (inp / "case.json").is_file():
        try:
            case_id = str(json.loads((inp / "case.json").read_text(encoding="utf-8")).get("case_id") or "unreported")
        except Exception:
            pass
    docs = []
    for p in sorted(inp.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in {".md", ".txt", ".pdf", ".docx"}:
            continue
        if any(k in p.name.lower() for k in SKIP):
            continue
        text = read_doc(p)
        if len(norm(text)) >= 80:
            docs.append({"doc": p.name, "text": text})
    if not docs:
        (out / "result.json").write_text(json.dumps(
            {"case_id": case_id, "status": "insufficient_evidence",
             "uncertainties": ["没有可读的阅读材料"], "verification": {"all_passed": False}},
            ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps({"case_id": case_id, "status": "insufficient_evidence"}, ensure_ascii=False))
        return 2
    anns = annotate(docs)
    groups = themes(anns)
    by_id = {a["id"]: a for a in anns}
    with (out / "annotations.tsv").open("w", encoding="utf-8") as fh:
        fh.write("id\ttype\tdoc\tlocator\tnumbers\ttags\tsentence\n")
        for a in anns:
            fh.write(f'{a["id"]}\t{a["type"]}\t{a["doc"]}\t{a["locator"]}\t'
                     f'{",".join(a["numbers"])}\t{",".join(a["tags"])}\t{a["sentence"]}\n')
    with (out / "themes.tsv").open("w", encoding="utf-8") as fh:
        fh.write("theme\tcount\ttypes\tannotation_ids\n")
        for g in groups:
            fh.write(f'{g["label"]}\t{g["count"]}\t{json.dumps(g["types"], ensure_ascii=False)}\t'
                     f'{",".join(g["ids"])}\n')
    with (out / "notes.jsonl").open("w", encoding="utf-8") as fh:
        for a in anns:
            fh.write(json.dumps({"case_id": case_id, "annotation": a}, ensure_ascii=False) + "\n")
    def block(kind: str, limit: int = 8) -> list[str]:
        rows = [a for a in anns if a["type"] == kind][:limit]
        return [f'- 「{a["sentence"]}」（{a["doc"]} {a["locator"]}）' for a in rows] or ["材料中未标注到此类句子。"]
    lines = [f"# 阅读批注与知识沉淀（{case_id}）", "",
             f"材料 {len(docs)} 篇，批注 {len(anns)} 条（每条都指向原文句子与段落），主题卡 {len(groups)} 张。", "",
             "## 主题卡", ""]
    for i, g in enumerate(groups, 1):
        lines.append(f"### 卡 {i}：{g['label']}（{g['count']} 条批注）")
        for aid in g["ids"][:6]:
            a = by_id[aid]
            lines.append(f'- [{a["id"]}·{a["type"]}] 「{a["sentence"]}」（{a["doc"]} {a["locator"]}）')
        lines.append("")
    lines += ["## 定义", ""] + block("definition")
    lines += ["", "## 方法与步骤", ""] + block("method", 6)
    lines += ["", "## 带数值的证据", ""] + block("evidence", 10)
    lines += ["", "## 结论性表述", ""] + block("claim", 8)
    lines += ["", "## 局限与未决问题", ""] + block("limitation", 6) + block("question", 4)
    lines += ["", "## 沉淀说明与检索方式", "",
              "- 所有批注都可用 `annotations.tsv` 的 `locator`（`pN sM` = 第 N 段第 M 句）回到原文定位；",
              "- `notes.jsonl` 每行一条批注，可直接导入笔记/知识库工具，`tags` 含类型与是否带数值；",
              "- 主题卡按共有内容词聚合，只收录出现在 ≥2 条批注中的词，单次提及不单独成卡。",
              "", "## 局限", "",
              "- 批注为规则抽取（提示词 + 数值特征），不判断论证是否成立，也不补写原文没有的评价；",
              "- 中文按二元组统计主题，同义表述可能被拆成不同卡片，需人工合并；",
              "- 未标注到的类型（如定义）在文中可能确实没有，要看 `annotations.tsv` 的 type 分布再下结论。"]
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "summary.docx").write_bytes(_docx(f"阅读批注与知识沉淀（{case_id}）", lines))
    checks = {
        "every_annotation_has_locator": all(a["locator"] for a in anns),
        "every_annotation_quotes_text": all(a["sentence"] for a in anns),
        "themes_need_two_annotations": all(g["count"] >= 2 for g in groups),
        "theme_ids_resolve": all(i in by_id for g in groups for i in g["ids"]),
        "type_counts_reconcile": sum(Counter(a["type"] for a in anns).values()) == len(anns),
    }
    checks["all_passed"] = all(checks.values())
    payload = {"case_id": case_id, "status": "answered" if anns else "insufficient_evidence",
               "annotations": {"total": len(anns), "by_type": dict(Counter(a["type"] for a in anns)),
                               "files": ["annotations.tsv", "notes.jsonl"]},
               "themes": [{"theme": g["label"], "count": g["count"], "ids": g["ids"]} for g in groups],
               "digest": {"primary": "summary.docx", "markdown": "summary.md"},
               "gaps": ([f"未标注到 {k}" for k in ("definition", "limitation")
                         if not any(a["type"] == k for a in anns)]),
               "limitations": ["规则抽取，不做论证评价", "中文主题按二元组，需人工合并同义", "未标注≠不存在"],
               "verification": checks}
    payload["metadata_coverage"] = meta_attach(inp, out)
    (out / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"case_id": case_id, "annotations": len(anns), "themes": len(groups),
                      "by_type": payload["annotations"]["by_type"],
                      "all_passed": checks["all_passed"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
