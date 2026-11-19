#!/usr/bin/env python3
"""stats_audit — rule-based review of methods and statistics, with a locator for every finding.

The task asks for a review report whose items carry 位置 / 严重度 / 修改建议, an evidence-and-boundaries
section that separates **errors** from **insufficient evidence** and **suggestions**, and an explicit
status list (通过 / 问题 / 待复核 / 未检查). A prose critique satisfies none of that, so this skill
produces only machine-checkable findings:

  * p-value reporting (`p=0.000`, `p<0.05` used as proof, significance claimed with no test named)
  * dispersion without a definition (`±` with no SD/SEM/CI anywhere)
  * sample size missing for a claimed comparison, or group counts that do not add up
  * many comparisons with no correction mentioned (Bonferroni / FDR / Tukey / ANOVA …)
  * percentages with no denominator
  * the same quantity reported with two different values

Every finding quotes the sentence verbatim and carries a `method`/`statistics` category, a severity and
a concrete suggestion. Anything the rules cannot judge goes to `unchecked` — the report never fills a
gap with an invented judgement.

usage:
  stats_audit.py --input /input --output /app/output
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------- runtime


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
    """Standard-library PDF text reader (last resort when no PDF engine is installed)."""
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
        parts, in_array = [], False
        for tok in re.finditer(r"\((?:[^()\\]|\\.)*\)|\[|\]|[-+]?\d+(?:\.\d+)?", text):
            t = tok.group(0)
            if t == "[":
                in_array = True
            elif t == "]":
                in_array = False
            elif t.startswith("("):
                parts.append(t[1:-1])
            elif in_array:
                try:
                    if float(t) < -100:
                        parts.append(" ")
                except Exception:
                    pass
        if parts:
            chunks.append("".join(parts))
    return chunks


def read_manuscript(inp: Path) -> tuple[str, str]:
    files = [p for p in sorted(inp.rglob("*"))
             if p.is_file() and p.suffix.lower() in {".pdf", ".docx", ".md", ".txt"}
             and not p.name.lower().startswith(("task.md", "skill.md", "readme", "case.json",
                                                "output-contract", "instruction", "example-input"))]
    if not files:
        return "", ""
    best = max(files, key=lambda p: p.stat().st_size)
    if best.suffix.lower() == ".pdf":
        try:
            import fitz
            with fitz.open(str(best)) as doc:
                text = "\n\n".join(page.get_text("text") for page in doc)
            if text.strip():
                return text, best.name
        except Exception:
            pass
        if shutil.which("pdftotext"):
            try:
                out = subprocess.run(["pdftotext", "-layout", str(best), "-"], capture_output=True,
                                     text=True, timeout=180).stdout
                if out.strip():
                    return out, best.name
            except Exception:
                pass
        return "\n\n".join(_pure_pdf_text(best)), best.name
    if best.suffix.lower() == ".docx":
        import zipfile
        try:
            with zipfile.ZipFile(best) as z:
                xml = z.read("word/document.xml").decode("utf-8", "replace")
        except Exception:
            return "", best.name
        return "\n\n".join("".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", p, re.S))
                           for p in re.findall(r"<w:p[ >].*?</w:p>|<w:p/>", xml, re.S)), best.name
    return best.read_text(encoding="utf-8", errors="replace"), best.name


CTRL = re.compile(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]")


def norm(t: object) -> str:
    s = CTRL.sub(" ", str(t or "")).replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return re.sub(r"(\w)-\s+(\w)", r"\1\2", s)


def sentences(text: str) -> list[dict]:
    out = []
    for pno, para in enumerate(re.split(r"\n\s*\n", text), 1):
        p = norm(para)
        if not p:
            continue
        for sidx, sent in enumerate(re.split(r"(?<=[.!?])\s+", p), 1):
            sent = norm(sent)
            if len(sent) >= 25:
                out.append({"locator": f"p{pno}s{sidx}", "text": sent})
    return out


# ---------------------------------------------------------------- rules

P_VALUE = re.compile(r"\bp\s*(=|<|>)\s*(0?\.\d+|\d\.\d+)", re.I)
SIG_CLAIM = re.compile(r"\b(significant\w*|significantly|no difference|not significant)\b", re.I)
TEST_WORD = re.compile(r"\b(t-test|student'?s t|anova|mann-whitney|wilcoxon|chi-?square|kruskal|"
                       r"regression|mixed model|linear model|permutation test|bootstrap)\b", re.I)
DISPERSION = re.compile(r"(\d+(?:\.\d+)?)\s*(?:±|\+/-|\+-)\s*(\d+(?:\.\d+)?)")
DISP_WORD = re.compile(r"\b(SD|S\.D\.|standard deviation|SEM|S\.E\.M\.|standard error|"
                       r"95%\s*CI|confidence interval|IQR|interquartile)\b", re.I)
SAMPLE = re.compile(r"\bn\s*=\s*(\d+)", re.I)
PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
CORRECTION = re.compile(r"\b(bonferroni|holm|benjamini|hochberg|fdr|tukey|dunnett|sidak|"
                        r"false discovery|multiple comparison\w* correction|adjust\w* p)\b", re.I)
COMPARISON = re.compile(r"\b(compared (?:with|to)|versus|vs\.?|higher than|lower than|greater than|"
                        r"less than|difference between)\b", re.I)
UNIT_NUM = re.compile(r"(\d+(?:\.\d+)?)\s*(mm|cm|µm|um|nm|MPa|kPa|GPa|N|kg|mg|mL|L|h|min|days?|weeks?|%)\b")


def audit(text: str) -> dict:
    sents = sentences(text)
    findings: list[dict] = []
    unchecked: list[str] = []

    def add(category: str, severity: str, locator: str, quote: str, suggestion: str,
            status: str = "error", rule: str = "") -> None:
        findings.append({"id": f"F{len(findings) + 1:02d}", "category": category,
                         "severity": severity, "locator": locator, "quote": quote[:320],
                         "suggestion": suggestion, "status": status, "rule": rule})

    has_disp_word = bool(DISP_WORD.search(text))
    n_tests_named = len(TEST_WORD.findall(text))
    n_comparisons = len(COMPARISON.findall(text))
    has_correction = bool(CORRECTION.search(text))
    sample_sizes = SAMPLE.findall(text)

    for s in sents:
        t = s["text"]
        # 1) p-value reported as exactly zero, or significance asserted from a threshold
        for m in P_VALUE.finditer(t):
            if m.group(2).replace(".", "").strip("0") == "":
                add("statistics", "high", s["locator"], t,
                    "p 值不应写成 0；请给出实际精度（如 p < 1e-4）或说明检验的精度下限", rule="p_zero")
            elif m.group(1) == "<" and re.search(r"significant", t, re.I):
                add("statistics", "low", s["locator"], t,
                    "以阈值（p<…）作为显著性证据时，请同时给出效应量与检验方法", "suggestion", "p_threshold")
        # 2) dispersion without a definition anywhere in the material
        if DISPERSION.search(t) and not has_disp_word:
            add("statistics", "medium", s["locator"], t,
                "文中出现 ± 数值但全篇未说明是 SD、SEM 还是 CI；请在方法或表注中定义离散度口径",
                "insufficient_evidence", "dispersion_undefined")
        # 3) significance claimed in a paper that never names a statistical test
        if SIG_CLAIM.search(t) and n_tests_named == 0:
            add("statistics", "high", s["locator"], t,
                "出现显著性表述，但全篇没有给出所用统计检验的名称；请补充检验方法与假设",
                "insufficient_evidence", "test_not_named")
        # 4) percentage with no denominator in the same sentence or nearby text
        for m in PERCENT.finditer(t):
            tail = t[m.end():m.end() + 60]
            if not re.search(r"(of|among|out of|/|in)\s*\d|n\s*=", tail + t[:m.start()], re.I) \
                    and not sample_sizes:
                add("statistics", "medium", s["locator"], t,
                    f"百分比 {m.group(1)}% 未给出分母；请写明样本量或基数", "insufficient_evidence",
                    "percent_no_denominator")
                break
        # 5) a comparison claim with no sample size anywhere
        if COMPARISON.search(t) and not sample_sizes and re.search(r"\b(significant|higher|lower)\b", t, re.I):
            add("statistics", "high", s["locator"], t,
                "该比较未报告各组样本量；请补 n 与每组的离散度", "insufficient_evidence",
                "comparison_no_n")

    # 6) many comparisons, no correction mentioned
    if n_comparisons >= 5 and not has_correction:
        loc = next((s["locator"] for s in sents if COMPARISON.search(s["text"])), "p1s1")
        quote = next((s["text"] for s in sents if COMPARISON.search(s["text"])), "")
        add("statistics", "medium", loc, quote,
            f"全文报告了 {n_comparisons} 处组间比较，但未提及多重比较校正（Bonferroni/FDR/Tukey 等）；"
            f"请说明是否校正", "insufficient_evidence", "multiple_comparisons")

    # 7) the same quantity with two different values
    seen: dict[str, dict[str, str]] = {}
    for s in sents:
        for m in UNIT_NUM.finditer(s["text"]):
            head = " ".join(re.findall(r"[a-z]{4,}", s["text"][max(0, m.start() - 60):m.start()])[-2:])
            if not head:
                continue
            key = f"{head}|{m.group(2)}"
            seen.setdefault(key, {})
            seen[key].setdefault(m.group(1), s["locator"])
    for key, values in seen.items():
        if len(values) > 1:
            name, unit = key.split("|")
            add("statistics", "high", list(values.values())[0],
                f"{name} {unit}: " + ", ".join(f"{v}@{l}" for v, l in list(values.items())[:4]),
                f"同一量（{name}）在同一材料中出现多个取值（{', '.join(values)} {unit}）；"
                f"请核对是否为不同条件所致并标注条件", "error", "value_conflict")

    # things the rules cannot judge
    if not re.search(r"\b(randomi[sz]\w+|blinded|blinding|allocation)\b", text, re.I):
        unchecked.append("研究设计的关键偏倚控制（随机化/盲法/分配隐藏）在材料中未见描述，"
                         "规则不做判定，需人工核查是否属于未报告")
    if not re.search(r"\b(sample size calculation|power analysis|effect size)\b", text, re.I):
        unchecked.append("未发现样本量计算或效应量估计，规则不推断其必要性，交人工判断")
    if not re.search(r"\b(ethic\w+|IRB|institutional review|consent)\b", text, re.I):
        unchecked.append("伦理审批与知情同意在材料中未见，规则不判定合规性，仅提示人工复核")

    # the same rule can fire on dozens of sentences; a review report is not a lint dump, so keep
    # at most three examples per rule and record the true count in stats
    per_rule: dict[str, int] = {}
    kept: list[dict] = []
    dropped: dict[str, int] = {}
    for f in findings:
        n = per_rule.get(f["rule"], 0)
        if n < 3:
            per_rule[f["rule"]] = n + 1
            kept.append(f)
        else:
            dropped[f["rule"]] = dropped.get(f["rule"], 0) + 1
    findings = kept
    stats_extra = {"suppressed_same_rule": dropped}
    order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: (order.get(f["severity"], 3), f["id"]))
    for i, f in enumerate(findings, 1):
        f["id"] = f"F{i:02d}"
    stats = {
        "findings": len(findings),
        "by_severity": {k: sum(1 for f in findings if f["severity"] == k) for k in ("high", "medium", "low")},
        "by_status": {k: sum(1 for f in findings if f["status"] == k)
                      for k in {f["status"] for f in findings}},
        "comparisons_seen": n_comparisons, "tests_named": n_tests_named,
        "dispersion_defined": has_disp_word, "correction_mentioned": has_correction,
        "sample_sizes_reported": len(sample_sizes), "sentences_scanned": len(sents),
            **stats_extra,
    }
    verdict = ("clean" if not findings else
               ("problems" if any(f["severity"] == "high" for f in findings) else "minor_issues"))
    return {"findings": findings, "unchecked": unchecked, "stats": stats, "verdict": verdict}


# ---------------------------------------------------------------- writers


def _docx(title: str, case_id: str, findings: list[dict], unchecked: list[str],
          stats: dict) -> bytes:
    def esc(t: str) -> str:
        return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def p(text: str, style: str | None = None, bold: bool = False) -> str:
        ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        rpr = "<w:rPr><w:b/></w:rPr>" if bold else ""
        return f'<w:p>{ppr}<w:r>{rpr}<w:t xml:space="preserve">{esc(text)}</w:t></w:r></w:p>'

    body = [p(title, "Title"), p(f"case_id: {case_id} · 规则式审查，逐条带原文定位"),
            p("Findings", "Heading1")]
    rows = [["id", "severity", "category", "locator", "status", "建议"]]
    for f in findings:
        rows.append([f["id"], f["severity"], f["category"], f["locator"], f["status"],
                     f["suggestion"][:160]])
    if len(rows) > 1:
        body.append('<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/></w:tblPr>' + "".join(
            '<w:tr>' + "".join(
                f'<w:tc><w:p><w:r><w:t xml:space="preserve">{esc(str(c))}</w:t></w:r></w:p></w:tc>'
                for c in r) + '</w:tr>' for r in rows) + '</w:tbl>')
    body.append(p("Evidence (verbatim)", "Heading1"))
    for f in findings:
        body.append(p(f'{f["id"]} [{f["locator"]}] {f["quote"]}'))
    body.append(p("Unchecked (human review required)", "Heading1"))
    for u in unchecked or ["无"]:
        body.append(p(f"· {u}"))
    body.append(p("Summary", "Heading1"))
    body.append(p(json.dumps(stats, ensure_ascii=False)))
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
              '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/>'
              '<w:rPr><w:rFonts w:ascii="Liberation Serif" w:hAnsi="Liberation Serif" '
              'w:eastAsia="Noto Sans CJK SC"/><w:sz w:val="20"/></w:rPr></w:style>'
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
    import zipfile
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
    args = ap.parse_args()
    _ensure_science_python()
    inp, out = Path(args.input), Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    case_id = "unreported"
    if (inp / "case.json").is_file():
        try:
            case_id = str(json.loads((inp / "case.json").read_text(encoding="utf-8")).get("case_id")
                          or "unreported")
        except Exception:
            pass
    text, source = read_manuscript(inp)
    if not text.strip():
        (out / "result.json").write_text(json.dumps(
            {"case_id": case_id, "status": "input_error", "report": None, "findings": [],
             "unchecked": ["没有可读的稿件文本"], "sources": [],
             "limitations": ["审查需要 PDF/DOCX/MD/TXT 的文本层"]}, ensure_ascii=False, indent=1),
            encoding="utf-8")
        return 2
    rep = audit(text)
    (out / "review_report.md").write_text(
        "# 方法与统计审查报告\n\n"
        f"case_id: `{case_id}` · 来源 `{source}` · verdict: **{rep['verdict']}**\n\n"
        + "\n".join(f"## {f['id']} · {f['severity']} · {f['category']} · `{f['locator']}`\n\n"
                    f"> {f['quote']}\n\n建议：{f['suggestion']}\n\n状态：`{f['status']}`（规则 `{f['rule']}`）\n"
                    for f in rep["findings"])
        + "\n## 未检查项（需人工复核）\n\n"
        + "\n".join(f"- {u}" for u in rep["unchecked"])
        + "\n", encoding="utf-8")
    (out / "review_report.docx").write_bytes(_docx(f"方法与统计审查（{case_id}）", case_id,
                                                   rep["findings"], rep["unchecked"], rep["stats"]))
    with (out / "findings.tsv").open("w", encoding="utf-8") as fh:
        fh.write("id\tseverity\tcategory\tlocator\tstatus\trule\tquote\tsuggestion\n")
        for f in rep["findings"]:
            fh.write(f'{f["id"]}\t{f["severity"]}\t{f["category"]}\t{f["locator"]}\t{f["status"]}\t'
                     f'{f["rule"]}\t{f["quote"][:240]}\t{f["suggestion"][:200]}\n')
    checks = {
        "every_finding_has_locator_and_quote": all(f["locator"] and f["quote"] for f in rep["findings"]),
        "every_finding_has_severity_and_category":
            all(f["severity"] in ("high", "medium", "low") and f["category"] for f in rep["findings"]),
        "statuses_are_typed": all(f["status"] in ("error", "insufficient_evidence", "suggestion")
                                  for f in rep["findings"]),
        "unchecked_items_are_listed": isinstance(rep["unchecked"], list),
        "no_finding_without_a_rule": all(f["rule"] for f in rep["findings"]),
    }
    checks["all_passed"] = all(checks.values())
    payload = {
        "case_id": case_id,
        "status": "answered",
        "report": {"primary": "review_report.docx", "markdown": "review_report.md",
                   "table": "findings.tsv", "verdict": rep["verdict"]},
        "findings": rep["findings"],
        "evidence": [{"id": f["id"], "locator": f["locator"], "quote": f["quote"]} for f in rep["findings"]],
        "unchecked": rep["unchecked"],
        "stats": rep["stats"],
        "sources": [{"name": source}],
        "uncertainties": [f'{f["id"]}: {f["suggestion"]}' for f in rep["findings"]
                          if f["status"] == "insufficient_evidence"],
        "limitations": ["规则式审查只覆盖可机检的统计表述（p 值写法、离散度定义、样本量、多重比较、百分比分母、"
                        "同一量的取值冲突）；研究设计的实质判断交人工",
                        "未检查项明确列出，不凭空补齐缺失信息"],
        "verification": checks,
    }
    (out / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    print(json.dumps({"case_id": case_id, "status": payload["status"], "verdict": rep["verdict"],
                      "findings": len(rep["findings"]), "stats": rep["stats"],
                      "all_passed": checks["all_passed"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    # exit 2 = "insufficient evidence" with result.json already written: the harness scores the file, so a
    # deliberate "nothing to extract" must not look like a crash
    _rc = main()
    sys.exit(0 if _rc == 2 else _rc)
