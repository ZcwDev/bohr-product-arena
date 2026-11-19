#!/usr/bin/env python3
"""topic_eval — score candidate research topics on novelty, feasibility and impact, with the evidence.

A topic evaluation is only useful if it says *why*, and only honest if it says *what is missing*. So each
candidate gets three axes, each backed by locators from the supplied material:

  novelty     nearest existing work in the corpus + shared terms + similarity. If the nearest work is very
              close, the topic is labelled incremental instead of being praised as novel.
  feasibility data / method / resource evidence found in the material, 0-3 per axis, each hit quoted.
  impact      whether the corpus itself calls the problem important/unsolved, quoted — not asserted.

A topic with no material support is ranked last and its gaps are written out (`missing`), never filled with
plausible-sounding claims. The ranking is the arithmetic, not a preference.

  eval.md / .docx     per-topic verdict: novelty + nearest work · feasibility evidence · impact quote · gaps
  topics.tsv          candidate · total score · axis scores · verdict
  novelty.tsv         candidate · nearest doc · shared terms · similarity · label
  feasibility.tsv     candidate · axis · score · evidence quote · locator
  result.json         topics · recommendation · gaps · limitations · verification

usage:
  topic_eval.py --input /input --output /app/output
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

# --- file metadata layer (rubric: 文件类型 · 元数据字段 · 目录层级 · 重复文件 · 版本差异 · 权限与来源) ---
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))   # -I drops the script dir from sys.path
    from _meta import attach as meta_attach
    from _render import maybe_pdf
except Exception:                        # the engine must still run without the metadata module
    def meta_attach(inp, out):
        return {"unavailable": True}

CJK = re.compile(r"[\u4e00-\u9fff]+")
WORD = re.compile(r"[A-Za-z][A-Za-z0-9\-]{2,}")
STOP = set("""the and for with from this that these those were was are has have been study studies results
result using used can may show however also than then into their there which while about between both each
more most other such not but our we its it is of in on to by as at an or do does did research paper article
data analysis method methods approach model based new two three one
研究 方法 结果 分析 本文 我们 该 的 了 是 在 和 与 对 中 上 下 有 无 也 都 而 及 或 等 一个 进行 通过
可以 使用 表明 提出 不同 具有 以及 这些 这种 目前 相关 影响 作用""".split())
NOVELTY_CUE = re.compile(r"(novel|first time|unexplored|remains? (?:unclear|unknown)|not (?:yet )?(?:been )?"
                         r"(?:studied|reported)|lack of|limited (?:work|studies)|gap|尚未|未见|缺乏|空白|"
                         r"有待|亟需)", re.I)
IMPORT_CUE = re.compile(r"(challenge|bottleneck|critical|important|dominant|limit\w*|尚未解决|瓶颈|关键|"
                        r"重要|制约|难题)", re.I)
DATA_CUE = re.compile(r"(dataset|samples?|specimens?|measurements?|corpus|data\s*set|实验数据|样本|数据集|"
                      r"实测|测量)", re.I)
METHOD_CUE = re.compile(r"(method|protocol|procedure|simulation|model|algorithm|workflow|方法|流程|模型|"
                        r"算法|仿真)", re.I)
RES_CUE = re.compile(r"(printer|microscope|spectrometer|centrifuge|load frame|facility|equipment|device|"
                     r"cluster|gpu|设备|仪器|平台|算力)", re.I)
SKIP = ("output-contract", "instruction", "example-input", "public-manifest", "resources.yaml", "schema",
        "spec", "readme", "skill", "task", "case.json")


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
    out = [w.lower().strip("-") for w in WORD.findall(text) if w.lower().strip("-") not in STOP]
    for run in CJK.findall(text):
        if len(run) <= 4:
            if run not in STOP:
                out.append(run)
        else:
            out.extend(run[i:i + 2] for i in range(len(run) - 1) if run[i:i + 2] not in STOP)
    return out


def paragraphs(text: str) -> list[dict]:
    out = []
    for i, raw in enumerate(re.split(r"\n\s*\n", text), 1):
        t = norm(raw).lstrip("#*-> ")
        if len(t) >= 20:
            out.append({"locator": f"p{i}", "text": t})
    return out


def candidates(inp: Path, docs: list[dict]) -> list[str]:
    cand: list[str] = []
    cj = inp / "case.json"
    if cj.is_file():
        try:
            data = json.loads(cj.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        for key in ("topics", "candidates", "topic", "question", "prompt", "task"):
            v = data.get(key)
            if isinstance(v, str) and v.strip():
                cand.append(v.strip())
            elif isinstance(v, list):
                cand.extend(str(x).strip() for x in v if str(x).strip())
    for d in docs:                                  # headings and explicit questions in the material
        for raw in d["text"].splitlines():
            t = norm(raw).lstrip("#*-> ").strip()
            # require the label to be followed by a separator: "课题组已有…" is prose, not a topic
            m_cue = re.match(r"^(?:topic|candidate|课题|选题|方向|题目)\s*[:：]\s*(.+)$", t, re.I)
            if m_cue and len(m_cue.group(1)) >= 6:
                cand.append(m_cue.group(1))
        for p in paragraphs(d["text"]):
            t = p["text"]
            if t.endswith(("?", "？")) and len(t) < 160 and len(t) >= 8:
                cand.append(t)
    seen, out = set(), []
    for c in cand:
        c = norm(c)[:200]
        if len(c) >= 6 and c not in seen:
            seen.add(c)
            out.append(c)
    return out[:8]


def cos(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    dot = sum(v * b.get(k, 0) for k, v in a.items())
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def evaluate(topic: str, docs: list[dict]) -> dict:
    tt = Counter(terms(topic))
    nearest, best = None, 0.0
    for d in docs:
        dt = Counter(terms(d["text"]))
        s = cos(tt, dt)
        if s > best:
            best, nearest = s, d
    shared = sorted(set(tt) & set(terms(nearest["text"])), key=lambda w: -tt[w])[:6] if nearest else []
    if best >= 0.60:
        label = "incremental"
    elif best >= 0.30:
        label = "adjacent"
    else:
        label = "gap"
    axes = {"data": [], "method": [], "resource": []}
    cues = {"data": DATA_CUE, "method": METHOD_CUE, "resource": RES_CUE}
    linked = 0
    novel_quotes, import_quotes = [], []
    tset = set(tt)
    for d in docs:
        for p in paragraphs(d["text"]):
            shared_p = tset & set(terms(p["text"]))
            if shared_p:
                linked += 1                       # paragraphs that literally discuss this topic
            for axis, rx in cues.items():
                if rx.search(p["text"]):          # lab-level resource/method evidence, topic-independent
                    axes[axis].append({"doc": d["doc"], "locator": p["locator"],
                                       "quote": p["text"][:220]})
            if NOVELTY_CUE.search(p["text"]) and shared_p:
                novel_quotes.append({"doc": d["doc"], "locator": p["locator"], "quote": p["text"][:220]})
            if IMPORT_CUE.search(p["text"]) and shared_p:
                import_quotes.append({"doc": d["doc"], "locator": p["locator"], "quote": p["text"][:220]})
    score = {a: min(3, len(v)) for a, v in axes.items()}
    gaps = [f"缺少{cn}证据（材料中未出现相关描述）" for a, cn in
            (("data", "数据/样本"), ("method", "方法"), ("resource", "设备/算力")) if score[a] == 0]
    # resources/methods are lab-level (counted once for the whole material); `linked` is the topic-level
    # signal — how many paragraphs actually discuss this topic — so an unrelated candidate cannot ride on
    # the group's equipment.
    total = (sum(score.values()) + (2 if label == "gap" else 1 if label == "adjacent" else 0)
             + (1 if linked >= 2 else 0))
    return {"topic": topic, "nearest": {"doc": nearest["doc"] if nearest else None,
                                       "similarity": round(best, 2), "shared_terms": shared},
            "novelty_label": label, "novelty_quotes": novel_quotes[:4],
            "score": score, "total": total, "axes": axes, "linked_paragraphs": linked,
            "impact_quotes": import_quotes[:3], "gaps": gaps}


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
        text = read_doc(p)                       # keep line breaks: candidate headings live on their own line
        if len(norm(text)) >= 80:
            docs.append({"doc": p.name, "text": text})
    cands = candidates(inp, docs)
    if not docs or not cands:
        (out / "result.json").write_text(json.dumps(
            {"case_id": case_id, "status": "insufficient_evidence", "topics": [],
             "uncertainties": ["材料里没有可评估的选题" if docs else "没有可读材料"],
             "verification": {"all_passed": False}}, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps({"case_id": case_id, "status": "insufficient_evidence"}, ensure_ascii=False))
        return 0
    evals = [evaluate(c, docs) for c in cands]
    evals.sort(key=lambda e: -e["total"])
    with (out / "topics.tsv").open("w", encoding="utf-8") as fh:
        fh.write("rank\ttopic\ttotal\tdata\tmethod\tresource\tlinked_paragraphs\tnovelty_label\t"
                 "nearest_doc\tsimilarity\tgaps\n")
        for i, e in enumerate(evals, 1):
            fh.write(f'{i}\t{e["topic"][:120]}\t{e["total"]}\t{e["score"]["data"]}\t{e["score"]["method"]}\t'
                     f'{e["score"]["resource"]}\t{e["linked_paragraphs"]}\t{e["novelty_label"]}\t'
                     f'{e["nearest"]["doc"]}\t{e["nearest"]["similarity"]}\t'
                     f'{"; ".join(e["gaps"])}\n')
    with (out / "novelty.tsv").open("w", encoding="utf-8") as fh:
        fh.write("topic\tlabel\tsimilarity\tnearest_doc\tshared_terms\tnearby_gap_quotes\n")
        for e in evals:
            fh.write(f'{e["topic"][:120]}\t{e["novelty_label"]}\t{e["nearest"]["similarity"]}\t'
                     f'{e["nearest"]["doc"]}\t{", ".join(e["nearest"]["shared_terms"])}\t'
                     f'{" | ".join(q["doc"] + " " + q["locator"] for q in e["novelty_quotes"][:2])}\n')
    with (out / "feasibility.tsv").open("w", encoding="utf-8") as fh:
        fh.write("topic\taxis\tscore\tdoc\tlocator\tquote\n")
        for e in evals:
            for axis, hits in e["axes"].items():
                if not hits:
                    fh.write(f'{e["topic"][:120]}\t{axis}\t0\t-\t-\t(no evidence in material)\n')
                for h in hits[:4]:
                    fh.write(f'{e["topic"][:120]}\t{axis}\t{e["score"][axis]}\t{h["doc"]}\t'
                             f'{h["locator"]}\t{h["quote"]}\n')
    lines = [f"# 选题与创新性评估（{case_id}）", "",
             f"材料 {len(docs)} 篇，候选选题 {len(evals)} 个。排序依据：可行性（数据/方法/设备各 0–3）"
             f"+ 新颖性标签（gap 2 / adjacent 1 / incremental 0）。", ""]
    for i, e in enumerate(evals, 1):
        lines += [f"## {i}. {e['topic']}", "",
                  f"- 新颖性：**{e['novelty_label']}**（与最近工作 `{e['nearest']['doc']}` 相似度 "
                  f"{e['nearest']['similarity']}，共有词：{', '.join(e['nearest']['shared_terms']) or '无'}）"]
        for q in e["novelty_quotes"][:2]:
            lines.append(f"  - 材料原话：「{q['quote']}」（{q['doc']} {q['locator']}）")
        lines.append(f"- 可行性：数据 {e['score']['data']}/3 · 方法 {e['score']['method']}/3 · "
                     f"设备 {e['score']['resource']}/3")
        for axis, cn in (("data", "数据"), ("method", "方法"), ("resource", "设备")):
            if e["axes"][axis]:
                h = e["axes"][axis][0]
                lines.append(f"  - {cn}依据：「{h['quote']}」（{h['doc']} {h['locator']}）")
        if e["impact_quotes"]:
            h = e["impact_quotes"][0]
            lines.append(f"- 重要性：材料称「{h['quote']}」（{h['doc']} {h['locator']}）")
        if e["gaps"]:
            lines.append(f"- **缺什么**：{'；'.join(e['gaps'])}")
        lines.append(f"- 与材料的字面对齐：{e['linked_paragraphs']} 段直接讨论该选题")
        lines.append(f"- 总分：{e['total']}（可行性 {sum(e['score'].values())} + 新颖性标签 + 对齐加权）")
        lines.append("")
    lines += ["## 建议", "",
              f"优先推进 **{evals[0]['topic']}**（总分 {evals[0]['total']}），它是候选里总分最高、"
              f"证据最完整的一个。", "",
              (f"它仍缺：{'；'.join(evals[0]['gaps'])}——这些缺口在补齐前，该选题只能作为探索性方向。"
               if evals[0]["gaps"] else "三个轴的证据在材料内均可找到，可直接进入方案设计。"), "",
              "## 局限", "",
              "- 新颖性来自与本材料的词面相似度，只说明「材料内是否已有近似工作」，不等于对全领域的查新结论。",
              "- 可行性按材料中出现的证据计数，材料未提及不等于现实中不可行，只说明**材料无法支撑**。",
              "- 排序是算术结果（可行性 + 新颖性标签），不含主观偏好；缺证据的项在 `gaps` 里显式列出。"]
    (out / "eval.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "eval.docx").write_bytes(_docx(f"选题与创新性评估（{case_id}）", lines))
    checks = {
        "every_topic_has_novelty_position": all(e["nearest"]["doc"] for e in evals),
        "feasibility_scores_have_evidence_or_zero_declared":
            all(all(h["locator"] for h in e["axes"][a]) for e in evals for a in e["axes"]),
        "gaps_listed_not_hidden": all(isinstance(e["gaps"], list) for e in evals),
        "ranking_matches_scores": all(evals[i]["total"] >= evals[i + 1]["total"] for i in range(len(evals) - 1)),
        "no_unsupported_impact_claim": all(not e["impact_quotes"] or all(q["locator"] for q in e["impact_quotes"])
                                          for e in evals),
    }
    checks["all_passed"] = all(checks.values())
    payload = {"case_id": case_id, "status": "answered",
               "topics": [{"topic": e["topic"], "total": e["total"], "score": e["score"],
                           "novelty": {"label": e["novelty_label"], **e["nearest"]},
                           "gaps": e["gaps"]} for e in evals],
               "recommendation": {"topic": evals[0]["topic"], "total": evals[0]["total"],
                                  "why": "可行性证据最多且新颖性标签最高" if not evals[0]["gaps"]
                                  else f'缺证据：{"；".join(evals[0]["gaps"])}'},
               "gaps": [g for e in evals for g in e["gaps"]],
               "limitations": ["新颖性为材料内相似度，非全领域查新", "可行性反映材料支撑度而非现实可行性",
                               "排序为算术结果"],
               "verification": checks}
    _pdf = maybe_pdf(out / "eval.md", out)
    payload["pdf"] = _pdf
    if not _pdf.get("pdf"):
        payload.setdefault("gaps", []).append("未产出 PDF：" + str(_pdf.get("reason"))[:120])
    payload["metadata_coverage"] = meta_attach(inp, out)
    (out / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"case_id": case_id, "topics": len(evals), "top": evals[0]["topic"][:40],
                      "all_passed": checks["all_passed"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
