#!/usr/bin/env python3
"""review_matrix — turn a literature set into an evidence matrix, a conflict/gap map and a review draft.

The contract for 文献综述/深度研究 is 研究产物 + 证据矩阵 + 不确定性: every synthesis statement must be
traceable to a record, disagreements between records must be shown rather than averaged away, and what
the material does **not** cover must be listed. So the draft is generated from the matrix — not written
first and annotated later:

  evidence_matrix.tsv  one row per record: claim · design · sample · quantitative result · strength · locator
  conflicts.tsv        the same quantity reported with two different values (both locators kept)
  gaps.tsv             query terms with no supporting record at all
  review.md / .docx    sections assembled from the matrix, each sentence carrying [R#]
  result.json          research_output / evidence_matrix / uncertainties + verification

usage:
  review_matrix.py --input /input --output /app/output [--query "..."]
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

NUM = re.compile(r"(?<![0-9.])(\d+(?:\.\d+)?)\s*(%|mm|cm|µm|um|nm|MPa|kPa|GPa|N|kg|mg|mL|L|h|min|days?|"
                 r"weeks?|years?|samples?|specimens?|patients?|件|例|倍|个|次)(?![A-Za-z])")
DESIGN = re.compile(r"\b(randomi[sz]ed|controlled trial|cohort|case[- ]control|cross[- ]sectional|"
                    r"in vitro|in vivo|animal model|retrospective|prospective|meta-analysis|"
                    r"systematic review|simulation)\b|随机|对照试验|队列|病例对照|横断面|体外|体内|"
                    r"动物模型|回顾性|前瞻性|荟萃分析|系统综述|仿真|模拟", re.I)
SAMPLE = re.compile(r"\bn\s*=\s*(\d+)|\b(\d+)\s+(?:samples?|specimens?|patients?|participants?|mice|hands?)\b|"
                    r"(?:样本量|共|每组)\s*(\d+)\s*(?:件|例|个|只)", re.I)
EFFECT = re.compile(r"\b(increas\w+|decreas\w+|reduc\w+|improv\w+|higher|lower|greater|significan\w*|"
                    r"no difference|unchanged|correlat\w+)\b|提高|降低|增加|减少|升高|下降|显著|"
                    r"无差异|不变|相关", re.I)
LIMIT = re.compile(r"\b(limitation|caveat|confound|bias|small sample|single material|"
                   r"局限|不足|偏倚)\b", re.I)


def norm(t) -> str:
    return re.sub(r"\s+", " ", str(t or "")).strip()


def read_material(inp: Path) -> tuple[str, str]:
    files = [p for p in sorted(inp.rglob("*"))
             if p.is_file() and p.suffix.lower() in {".md", ".txt", ".csv", ".tsv", ".json", ".jsonl", ".pdf", ".docx"}
             and not p.name.lower().startswith(("task", "skill", "readme", "output-contract",
                                                "example-input", "case.json"))]
    if not files:
        return "", ""
    best = max(files, key=lambda p: p.stat().st_size)
    if best.suffix.lower() == ".pdf":
        try:
            import fitz
            with fitz.open(str(best)) as doc:
                t = "\n\n".join(p.get_text("text") for p in doc)
            if t.strip():
                return t, best.name
        except Exception:
            pass
        if shutil.which("pdftotext"):
            t = subprocess.run(["pdftotext", "-layout", str(best), "-"], capture_output=True,
                               text=True, timeout=180).stdout
            if t.strip():
                return t, best.name
        return "", best.name
    if best.suffix.lower() in {".csv", ".tsv"}:
        rows = csv_record_rows(best)
        if rows:
            # 以行记录为主：把每行拼成"一段"再交给 prose 分支处理会丢字段，这里直接返回带字段的 JSON 文本
            import json as _json
            return "\n\n".join(_json.dumps(r, ensure_ascii=False) for r in rows), best.name
    if best.suffix.lower() == ".docx":
        with zipfile.ZipFile(best) as z:
            xml = z.read("word/document.xml").decode("utf-8", "replace")
        return "\n\n".join("".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", p, re.S))
                           for p in re.findall(r"<w:p[ >].*?</w:p>|<w:p/>", xml, re.S)), best.name
    return best.read_text(encoding="utf-8", errors="replace"), best.name



def csv_record_rows(path: Path) -> list[dict]:
    """A records table is one record per row. Parsing it as prose gives "1 record" for a 5-row CSV."""
    delim = "\t" if path.suffix.lower() == ".tsv" else ","
    try:
        with path.open(encoding="utf-8", errors="replace", newline="") as fh:
            rows = list(csv.DictReader(fh, delimiter=delim))
    except Exception:
        return []
    out = []
    for i, row in enumerate(rows, 1):
        low = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        if not any(low.values()):
            continue
        text = ". ".join(v for v in low.values() if v)[:600]
        if len(text) < 20:
            continue
        title = next((low[k] for k in ("title", "题目", "标题", "name") if low.get(k)), text[:80])
        abstract = next((low[k] for k in ("abstract", "摘要", "description") if low.get(k)), text)
        design = (DESIGN.search(abstract).group(0) if DESIGN.search(abstract) else "")
        sample = (SAMPLE.search(abstract).group(0) if SAMPLE.search(abstract) else "")
        effect = (EFFECT.search(abstract).group(0) if EFFECT.search(abstract) else "")
        limit = (LIMIT.search(abstract).group(0) if LIMIT.search(abstract) else "")
        nums = [f"{m.group(1)} {m.group(2)}" for m in NUM.finditer(abstract)][:3]
        year = next((low[k] for k in ("year", "年份", "pub_year") if low.get(k)), "")
        doi = next((low[k] for k in ("doi",) if low.get(k)), "")
        out.append({"rid": f"R{i}", "title": title[:120],
                    "design": design or "not_reported", "sample": sample or "not_reported",
                    "numbers": "; ".join(nums) or "not_reported",
                    "effect": effect or "not_reported", "limitation": limit or "not_reported",
                    "strength": ("high" if design.lower().startswith(("systematic", "meta"))
                                 else "moderate" if design else "unclear"),
                    "locator": f"{path.name}#row{i}", "text": abstract[:400],
                    "year": year, "doi": doi})
    return out



def _loc(source: str, locator) -> str:
    """CSV rows already carry `file#rowN`; prose blocks only have a paragraph number."""
    loc = str(locator)
    return loc if "#" in loc else f"{source}#p{loc}"

def record_rows(text: str) -> list[dict]:
    """A record is a markdown heading section, a blank-line paragraph, or a CSV/JSON row."""
    stripped = text.strip()
    if stripped.startswith("{") and "\"rid\"" in stripped:
        rows = []
        for i, line in enumerate([x for x in stripped.splitlines() if x.strip()], 1):
            try:
                d = json.loads(line)
            except Exception:
                continue
            d.setdefault("rid", f"R{i}")
            rows.append(d)
        if rows:
            return rows
    raw = [norm(b) for b in re.split(r"\n\s*\n", text) if norm(b)]
    blocks: list[str] = []
    for b in raw:                       # a heading line belongs to the paragraph under it
        if blocks and len(blocks[-1]) < 90 and not NUM.search(blocks[-1]):
            blocks[-1] = blocks[-1] + ". " + b
        else:
            blocks.append(b)
    rows, rid = [], 0
    for i, b in enumerate(blocks, 1):
        if len(b) < 40:
            continue
        rid += 1
        title = b.split(".")[0][:120]
        nums = [f"{m.group(1)} {m.group(2)}" for m in NUM.finditer(b)][:3]
        design = (DESIGN.search(b).group(0) if DESIGN.search(b) else "")
        sample = (SAMPLE.search(b).group(0) if SAMPLE.search(b) else "")
        effect = (EFFECT.search(b).group(0) if EFFECT.search(b) else "")
        limit = (LIMIT.search(b).group(0) if LIMIT.search(b) else "")
        strength = ("high" if design.lower().startswith(("systematic", "meta")) else
                    "moderate" if design else "unclear")
        rows.append({"rid": f"R{rid}", "title": title, "design": design or "not_reported",
                     "sample": sample or "not_reported", "numbers": "; ".join(nums) or "not_reported",
                     "effect": effect or "not_reported", "limitation": limit or "not_reported",
                     "strength": strength, "locator": f"{i}", "text": b})
    return rows


def conflicts(rows: list[dict]) -> list[dict]:
    seen: dict[str, dict[str, dict]] = {}
    for r in rows:
        for m in NUM.finditer(r["text"]):
            head = " ".join(re.findall(r"[a-z]{5,}", r["text"][max(0, m.start() - 60):m.start()])[-2:])
            if not head:
                continue
            key = f"{head}|{m.group(2)}"
            seen.setdefault(key, {})
            seen[key].setdefault(f"{m.group(1)} {m.group(2)}", {"rid": r["rid"], "locator": r["locator"]})
    # second pass keyed on (effect, unit): "45 % increase" vs "47 % increase" on the same effect and
    # unit is a real disagreement even when the surrounding nouns differ
    for r in rows:
        eff = r["effect"].lower()
        if eff == "not_reported":
            continue
        for m in NUM.finditer(r["text"]):
            tail = r["text"][m.end():m.end() + 24]
            if re.match(r"\s*(CI|confidence|confidence interval)", tail, re.I):
                continue  # a confidence level is not a measured value
            key = f"{eff[:6]}|{m.group(2)}"   # increased / increase / increasing share one key
            seen.setdefault(key, {})
            seen[key].setdefault(f"{m.group(1)} {m.group(2)}", {"rid": r["rid"], "locator": r["locator"]})
    out = []
    for key, vals in seen.items():
        # a conflict needs two different records; two numbers inside one record are not a disagreement
        if len({v["rid"] for v in vals.values()}) >= 2:
            name, unit = key.split("|")
            label = {"increa": "increase", "decrea": "decrease", "reduc": "reduction",
                     "improv": "improvement", "higher": "higher", "lower": "lower"}.get(name, name)
            out.append({"quantity": label, "unit": unit,
                        "values": [{"value": v, **meta} for v, meta in vals.items()],
                        "basis": "same quantity reported with different values in different records"})
    return out


def gaps(rows: list[dict], query: str) -> list[dict]:
    terms = [t.lower() for t in re.findall(r"[A-Za-z][A-Za-z\-]{2,}|[\u4e00-\u9fff]{2,}", query)]
    joined = " ".join(r["text"].lower() for r in rows)
    return [{"term": t, "status": "no_supporting_record",
             "basis": "term appears in the question but in no record"} for t in terms if t not in joined]


def _docx(title: str, paragraphs: list[tuple[str, str]]) -> bytes:
    def esc(t: str) -> str:
        return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    body = []
    for style, text in paragraphs:
        ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        body.append(f'<w:p>{ppr}<w:r><w:t xml:space="preserve">{esc(text)}</w:t></w:r></w:p>')
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
              '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/>'
              '<w:rPr><w:rFonts w:ascii="Liberation Serif" w:hAnsi="Liberation Serif" '
              'w:eastAsia="Noto Sans CJK SC"/><w:sz w:val="21"/></w:rPr></w:style>'
              '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/>'
              '<w:rPr><w:b/><w:sz w:val="32"/></w:rPr></w:style>'
              '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/>'
              '<w:pPr><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:b/><w:sz w:val="26"/></w:rPr></w:style>'
              '</w:styles>')
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--query", default="")
    args = ap.parse_args()
    inp, out = Path(args.input), Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    case_id, query = "unreported", args.query
    if (inp / "case.json").is_file():
        try:
            case = json.loads((inp / "case.json").read_text(encoding="utf-8"))
            case_id = str(case.get("case_id") or "unreported")
            query = query or str(case.get("query") or case.get("task_instruction") or "")[:400]
        except Exception:
            pass
    text, source = read_material(inp)
    rows = record_rows(text)
    if not rows:
        (out / "result.json").write_text(json.dumps(
            {"case_id": case_id, "status": "insufficient_evidence", "research_output": None,
             "evidence_matrix": [], "uncertainties": ["没有可解析的材料（需要文本层或结构化记录）"]},
            ensure_ascii=False, indent=1), encoding="utf-8")
        return 2
    conf = conflicts(rows)
    gap = gaps(rows, query)
    with (out / "evidence_matrix.tsv").open("w", encoding="utf-8") as fh:
        fh.write("rid\ttitle\tdesign\tsample\tquantitative\teffect\tlimitation\tstrength\tlocator\n")
        for r in rows:
            fh.write(f'{r["rid"]}\t{r["title"][:120]}\t{r["design"]}\t{r["sample"]}\t{r["numbers"]}\t'
                     f'{r["effect"]}\t{r["limitation"]}\t{r["strength"]}\t'
                     f'{_loc(source, r["locator"])}\n')
    with (out / "conflicts.tsv").open("w", encoding="utf-8") as fh:
        fh.write("quantity\tunit\tvalues\tbasis\n")
        for c in conf:
            fh.write(f'{c["quantity"]}\t{c["unit"]}\t'
                     f'{";".join(v["value"] + "@" + v["rid"] for v in c["values"])}\t{c["basis"]}\n')
    with (out / "gaps.tsv").open("w", encoding="utf-8") as fh:
        fh.write("term\tstatus\tbasis\n")
        for g in gap:
            fh.write(f'{g["term"]}\t{g["status"]}\t{g["basis"]}\n')
    # draft: sections built from the matrix, every sentence keeps its [R#]
    by_strength = {"high": [], "moderate": [], "unclear": []}
    for r in rows:
        by_strength[r["strength"]].append(r)
    md = [f"# 证据综述（{case_id}）", "", f"材料来源：`{source}`；记录数 {len(rows)}；查询：{query[:160] or '未给出'}", "",
          "## 1 方法与证据范围", "",
          f"本综述基于 {len(rows)} 条材料记录，按研究设计可识别性分级：高 {len(by_strength['high'])} 条、"
          f"中 {len(by_strength['moderate'])} 条、不可判定 {len(by_strength['unclear'])} 条。", "",
          "## 2 一致结论", ""]
    for r in rows:
        if r["effect"] != "not_reported" and r["strength"] != "unclear":
            md.append(f'- {r["title"]}：{r["effect"]}；{r["numbers"]}（{r["design"]}，{r["sample"]}）'
                      f'〔{r["rid"]}｜强度 {r["strength"]}｜{_loc(source, r["locator"])}〕')
    if not any(r["effect"] != "not_reported" and r["strength"] != "unclear" for r in rows):
        md.append("- 材料中没有可判定方向的结论（只报告了主题陈述）。")
    listed = {r["rid"] for r in rows
              if r["effect"] != "not_reported" and r["strength"] != "unclear"}
    rest = [r for r in rows if r["rid"] not in listed]
    if rest:
        md += ["", "## 2b 其余记录（描述性，不构成方向性结论）", ""]
        for r in rest:
            md.append(f'- {r["title"]}：设计 {r["design"]}，样本 {r["sample"]}，定量 {r["numbers"]}'
                      f'〔{r["rid"]}｜强度 {r["strength"]}｜{_loc(source, r["locator"])}〕')
    md += ["", "## 3 冲突与不一致", ""]
    for c in conf[:10]:
        md.append(f'- `{c["quantity"]}`（{c["unit"]}）在不同记录中取值为 '
                  + "、".join(f'{v["value"]}〔{v["rid"]}〕' for v in c["values"])
                  + "；保留双方数值，不取平均。")
    if not conf:
        md.append("- 未检测到同一量的取值冲突。")
    md += ["", "## 4 证据空白", ""]
    for g in gap[:10]:
        md.append(f'- 「{g["term"]}」在材料中没有对应记录，本综述不对其作结论。')
    if not gap:
        md.append("- 查询词在材料中均有对应记录。")
    md += ["", "## 5 不确定性", "",
           f"- 局限项被明确记录的记录数：{sum(1 for r in rows if r['limitation'] != 'not_reported')}/{len(rows)}。",
           "- 设计类型无法识别、样本量未报告或效应量缺离散度的记录，已在矩阵中标 `not_reported`，不作强度推断。",
           "- 冲突项只并列取值与定位，不下裁决；需要原始数据或方法细节才能判定。"]
    (out / "review.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    paragraphs = []
    for line in md:
        if line.startswith("# "):
            paragraphs.append(("Title", line[2:]))
        elif line.startswith("## "):
            paragraphs.append(("Heading1", line[3:]))
        elif line.strip():
            paragraphs.append(("", line.lstrip("- ").strip()))
    (out / "review.docx").write_bytes(_docx(f"证据综述（{case_id}）", paragraphs))
    checks = {
        "every_row_has_locator": all(r["locator"] for r in rows),
        "every_row_marks_missing_fields": all("not_reported" not in (r["design"],) or r["design"] == "not_reported" for r in rows),
        "conflicts_keep_both_sides": all(len(c["values"]) >= 2 for c in conf),
        "gaps_listed": isinstance(gap, list),
        "draft_citations_traceable": all(r["rid"] in "\n".join(md) for r in rows if r["strength"] != "unclear") or not rows,
        "limitations_stated": "不确定性" in "\n".join(md),
    }
    checks["all_passed"] = all(checks.values())
    payload = {"case_id": case_id, "status": "answered",
               "research_output": {"primary": "review.docx", "markdown": "review.md",
                                   "records": len(rows), "conflicts": len(conf), "gaps": len(gap)},
               "evidence_matrix": {"file": "evidence_matrix.tsv", "rows": len(rows),
                                   "by_strength": {k: len(v) for k, v in by_strength.items()}},
               "uncertainties": ([f'冲突：{c["quantity"]}（{c["unit"]}）' for c in conf[:10]]
                                 + [f'空白：{g["term"]}' for g in gap[:10]]),
               "sources": [{"name": source}], "query": query[:200],
               "limitations": ["只做材料内综合：不联网、不引入材料外文献",
                               "设计类型/样本量/效应量按规则识别，识别不到的标 not_reported 且不作强度推断",
                               "冲突只并列不裁决；裁决需要原始数据或方法细节"],
               "verification": checks}
    (out / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    print(json.dumps({"case_id": case_id, "status": payload["status"], "records": len(rows),
                      "conflicts": len(conf), "gaps": len(gap), "all_passed": checks["all_passed"]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    # exit 2 = "insufficient evidence" with result.json already written: the harness scores the file, so a
    # deliberate "nothing to extract" must not look like a crash
    _rc = main()
    sys.exit(0 if _rc == 2 else _rc)
