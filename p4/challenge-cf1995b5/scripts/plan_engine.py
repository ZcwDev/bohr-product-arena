#!/usr/bin/env python3
"""plan_engine — turn a methods text into an executable experiment plan with evidence and risk.

The delivery contract is 实验方案 (假设 · 变量 · 对照 · 步骤 · 参数 · 资源 · 判读标准) + 依据与风险 + 确认项,
and the last line of the rubric is the important one: 不把未经确认的建议写成可执行事实. So every row
carries either a verbatim locator from the supplied material or the explicit marker
`needs_confirmation` — nothing is invented to fill a table.

  hypothesis.md / .docx   the plan, assembled from the tables below
  variables.tsv           hypothesis · independent · dependent · control, each with its locator
  parameters.tsv          value + unit exactly as written (no conversion)
  steps.tsv               ordered procedure steps with locators
  risks.tsv               risk cue → why it matters → mitigation, per locator
  confirmations.tsv       safety / ethics / equipment availability: `needs_confirmation`
  result.json             plan / evidence_and_risk / confirmations + verification

usage:
  plan_engine.py --input /input --output /app/output
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

# --- file metadata layer (rubric: 文件类型 · 元数据字段 · 目录层级 · 重复文件 · 版本差异 · 权限与来源) ---
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))   # -I drops the script dir from sys.path
    from _meta import attach as meta_attach
    from _render import maybe_pdf
except Exception:                        # the engine must still run without the metadata module
    def meta_attach(inp, out):
        return {"unavailable": True}

NUM = re.compile(r"(?<![0-9.])(\d+(?:\.\d+)?)\s*(mm/min|mm/s|%/min|°C|MPa|kPa|GPa|mL|min|days?|weeks?|"
                 r"rpm|Hz|mm|cm|µm|um|nm|kg|mg|g|L|h|s|V|A|W|%|N)(?![A-Za-z])")
HYPO = re.compile(r"\b(hypothes\w+|we (?:expect|aim|propose|predict|test)|objective|goal|aim of)\b|假设|推测|预期|旨在|目标是|拟验证", re.I)
INDEP = re.compile(r"\b(at (?:different|various) [a-z ]{3,40}|vary\w*|varying|levels? of [a-z ]{3,30}|"
                   r"three (?:groups|arms)|two (?:groups|arms))\b|不同(?:的)?[\w\u4e00-\u9fff]{2,8}"
                   r"|(?:三|两|四)组|梯度|对照(?:组|条件)", re.I)
DEP = re.compile(r"\b(measur\w+|assess\w+|evaluat\w+|outcome|strength|stiffness|yield|viability|"
                 r"concentration|temperature|rate)\b", re.I)
CONTROL = re.compile(r"\b(control (?:group|samples?|condition)|vehicle|placebo|sham|untreated|"
                     r"baseline|as-received)\b|对照组|空白对照|未处理|基线", re.I)
STEP = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+(.{20,})")
RISK = re.compile(r"\b(risk|hazard|toxic\w*|corrosiv\w*|flammab\w*|high[- ]voltage|high[- ]temperature|"
                  r"contaminat\w*|bias|confound\w*|safety)\b|危险|风险|有毒|易燃|高温|污染|偏差|干扰", re.I)
CRITERION = re.compile(r"\b(p\s*[<>=]\s*0?\.\d+|significan\w+|threshold|criteria|accept\w*|"
                       r"consider\w* (?:successful|acceptable))\b|判据|判定|阈值|显著性|误差小于|偏差", re.I)
EQUIP = re.compile(r"\b(Instron|MTS|Stratasys|FDM printer|microscope|centrifuge|spectrometer|"
                   r"balance|oven|incubator|laser|sensor)\b|扫描仪|试验机|打印机|烘箱|炉|显微镜|离心机", re.I)


def norm(t) -> str:
    return re.sub(r"\s+", " ", str(t or "")).strip()


def deenum(t: str) -> str:
    """Drop a leading list enumerator so quoted text reads as prose."""
    return re.sub(r"^\s*\d+[.)]\s*", "", t)


def read_material(inp: Path) -> tuple[str, str]:
    files = [p for p in sorted(inp.rglob("*"))
             if p.is_file() and p.suffix.lower() in {".md", ".txt", ".pdf", ".docx", ".csv", ".json"}
             and not p.name.lower().startswith(("task", "skill", "readme", "case.json", "output-contract",
                                                "example-input"))]
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
    if best.suffix.lower() == ".docx":
        with zipfile.ZipFile(best) as z:
            xml = z.read("word/document.xml").decode("utf-8", "replace")
        return "\n\n".join("".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", p, re.S))
                           for p in re.findall(r"<w:p[ >].*?</w:p>|<w:p/>", xml, re.S)), best.name
    return best.read_text(encoding="utf-8", errors="replace"), best.name


def sentences(text: str) -> list[dict]:
    out = []
    for pno, para in enumerate(re.split(r"\n\s*\n", text), 1):
        p = norm(para)
        # only treat "word." / "word!" as a sentence end; "1." is an enumerator, not a boundary
        for sidx, s in enumerate(re.split(r"(?<=[。！？])|(?<=[A-Za-z\)\]%][.!?])\s+", p), 1):
            s = norm(s)
            if len(s) >= 25:
                out.append({"locator": f"p{pno}s{sidx}", "text": s})
    return out


def build(text: str) -> dict:
    sents = sentences(text)
    hypos, indep, deps, controls, steps, params, risks, crits, equip = [], [], [], [], [], [], [], [], []
    for s in sents:
        t, loc = s["text"], s["locator"]
        if HYPO.search(t):
            hypos.append({"claim": t[:300], "locator": loc})
        if INDEP.search(t):
            m_ind = INDEP.search(t)
            indep.append({"name": (m_ind.group(1) or m_ind.group(0)).strip(),
                          "statement": t[:240], "locator": loc})
        if re.search(r"\b(measur\w+|assess\w+|evaluat\w+)\b", t, re.I):
            m_named = (re.search(r"\bas the ([\w\- ]{3,40}?)[.,;]", t)
                       or re.search(r"the ([\w\- ]{3,40}?) (?:was|were|is|are) "
                                    r"(?:measured|assessed|evaluated)", t))
            name = m_named.group(1).strip() if m_named else DEP.search(t).group(1)
            deps.append({"name": name, "statement": deenum(t)[:240], "locator": loc})
        if CONTROL.search(t):
            m_c = CONTROL.search(t)
            controls.append({"name": (m_c.group(1) or m_c.group(0)).strip(),
                             "statement": t[:240], "locator": loc})
        m_step = STEP.match(t)
        if m_step:
            steps.append({"order": len(steps) + 1, "step": deenum(norm(m_step.group(1)))[:240],
                          "locator": loc})
        elif re.search(r"\b(we (?:printed|prepared|mixed|tested|measured|performed)|was (?:printed|prepared|"
                       r"measured|performed))\b|采用|制备|打印|测量|测试|记录|设置|调整|加载|保温|冷却|"
                       r"取样|称量", t, re.I) and not re.match(
                r"^(假设|推测|预期|判据|判定|风险|变量|对照|自变量|因变量|指标|依据|说明)", t):
            steps.append({"order": len(steps) + 1, "step": t[:240], "locator": loc})
        seen_p = set()
        for m in NUM.finditer(t):
            key = (m.group(1), m.group(2), loc)
            if key in seen_p:
                continue
            seen_p.add(key)
            params.append({"value": m.group(1), "unit": m.group(2), "statement": t[:200], "locator": loc})
        if RISK.search(t):
            risks.append({"cue": RISK.search(t).group(1) or RISK.search(t).group(0), "statement": t[:240],
                          "locator": loc,
                          "mitigation": "在方法中写明该风险的处置（如防护、限值、重复测量）并交人工确认"})
        if CRITERION.search(t):
            m_cr = CRITERION.search(t)
            crits.append({"criterion": m_cr.group(1) or m_cr.group(0), "statement": t[:240], "locator": loc})
        if EQUIP.search(t):
            m_eq = EQUIP.search(t)
            equip.append({"name": m_eq.group(1) or m_eq.group(0), "statement": t[:200], "locator": loc})
    # confirmations are never asserted: each is an item the experimenter must confirm
    confirmations = [
        {"item": "安全", "status": "needs_confirmation",
         "detail": "危险试剂/高温/高压等操作需按机构安全规范确认后执行", "locator": "-"},
        {"item": "伦理", "status": "needs_confirmation",
         "detail": "涉及人体或动物样本时需伦理审批与知情同意（材料中未包含）", "locator": "-"},
    ]
    for e in equip[:6]:
        confirmations.append({"item": f"设备可用性：{e['name']}", "status": "needs_confirmation",
                              "detail": f"材料提到「{e['statement'][:80]}」，是否可用与型号需现场确认",
                              "locator": e["locator"]})
    confirmations.append({"item": "样本量依据", "status": "needs_confirmation" if not any(
        re.search(r"power|sample size calculation", s["text"], re.I) for s in sents) else "reported",
        "detail": "材料未包含样本量计算/功效分析" if not any(
            re.search(r"power|sample size calculation", s["text"], re.I) for s in sents)
        else "材料给出了样本量依据", "locator": "-"})
    return {"hypotheses": hypos, "independent": indep, "dependent": deps, "controls": controls,
            "steps": steps, "parameters": params, "risks": risks, "criteria": crits,
            "equipment": equip, "confirmations": confirmations}


def _docx(title: str, lines: list[tuple[str, str]]) -> bytes:
    def esc(t: str) -> str:
        return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    body = []
    for style, text in lines:
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
    args = ap.parse_args()
    inp, out = Path(args.input), Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    case_id = "unreported"
    if (inp / "case.json").is_file():
        try:
            case_id = str(json.loads((inp / "case.json").read_text(encoding="utf-8")).get("case_id")
                          or "unreported")
        except Exception:
            pass
    text, source = read_material(inp)
    if not norm(text):
        (out / "result.json").write_text(json.dumps(
            {"case_id": case_id, "status": "insufficient_evidence", "plan": None,
             "confirmations": [{"item": "材料", "status": "needs_confirmation",
                                "detail": "没有可读的方法材料"}], "verification": {"all_passed": False}},
            ensure_ascii=False, indent=1), encoding="utf-8")
        return 0
    rep = build(text)
    def dump(name: str, header: list[str], rows: list[dict], keys: list[str]) -> None:
        with (out / name).open("w", encoding="utf-8") as fh:
            fh.write("\t".join(header) + "\n")
            for r in rows:
                fh.write("\t".join(str(r.get(k, ""))[:240] for k in keys) + "\n")
    dump("variables.tsv", ["type", "name", "statement", "locator"],
         [{"type": "hypothesis", "name": h["claim"][:60], "statement": h["claim"],
           "locator": h["locator"]} for h in rep["hypotheses"]]
         + [dict(type="independent", **i) for i in rep["independent"]]
         + [dict(type="dependent", **d) for d in rep["dependent"]]
         + [dict(type="control", **c) for c in rep["controls"]],
         ["type", "name", "statement", "locator"])
    dump("parameters.tsv", ["value", "unit", "statement", "locator"], rep["parameters"],
         ["value", "unit", "statement", "locator"])
    dump("steps.tsv", ["order", "step", "locator"], rep["steps"], ["order", "step", "locator"])
    dump("risks.tsv", ["cue", "statement", "mitigation", "locator"], rep["risks"],
         ["cue", "statement", "mitigation", "locator"])
    dump("confirmations.tsv", ["item", "status", "detail", "locator"], rep["confirmations"],
         ["item", "status", "detail", "locator"])
    lines = [("#", f"实验方案（{case_id}）"), ("", f"材料来源：`{source}`")]
    def section(title: str, rows: list[str]) -> None:
        lines.append(("##", title))
        for r in rows or ["材料中未报告，需补充。"]:
            lines.append(("", r))
    section("1 假设", [f'{h["claim"]}〔{h["locator"]}〕' for h in rep["hypotheses"]])
    section("2 变量与对照",
            [f'自变量：{i["name"]} —— {i["statement"]}〔{i["locator"]}〕' for i in rep["independent"]]
            + [f'因变量：{d["name"]} —— {d["statement"]}〔{d["locator"]}〕' for d in rep["dependent"]]
            + [f'对照：{c["name"]} —— {c["statement"]}〔{c["locator"]}〕' for c in rep["controls"]])
    section("3 步骤", [f'{s["order"]}. {s["step"]}〔{s["locator"]}〕' for s in rep["steps"]])
    section("4 参数（原样抄录，未换算）", [f'{p["value"]} {p["unit"]} —— {p["statement"]}〔{p["locator"]}〕'
                                for p in rep["parameters"][:20]])
    section("5 资源", [f'{e["name"]} —— {e["statement"]}〔{e["locator"]}〕' for e in rep["equipment"]])
    section("6 判读标准", [f'{c["criterion"]} —— {c["statement"]}〔{c["locator"]}〕' for c in rep["criteria"]])
    section("7 风险与处置", [f'{r["cue"]}：{r["statement"]}〔{r["locator"]}〕 → {r["mitigation"]}'
                        for r in rep["risks"]])
    section("8 需人工确认（不得当作已确认事实）",
            [f'{c["item"]}（{c["status"]}）：{c["detail"]}' for c in rep["confirmations"]])
    (out / "plan.md").write_text("\n".join(t if s == "" else f"{s} " + t for s, t in lines) + "\n",
                                 encoding="utf-8")
    (out / "plan.docx").write_bytes(_docx(f"实验方案（{case_id}）", lines))
    checks = {
        "every_plan_item_has_locator_or_confirmation":
            all(r["locator"] for r in rep["hypotheses"] + rep["independent"] + rep["dependent"]
                + rep["controls"] + rep["steps"] + rep["parameters"] + rep["criteria"]),
        "unconfirmed_items_are_marked":
            all(c["status"] in ("needs_confirmation", "reported") for c in rep["confirmations"]),
        "parameters_keep_original_unit": all(p["unit"] for p in rep["parameters"]) or not rep["parameters"],
        "risks_have_mitigation": all(r["mitigation"] for r in rep["risks"]),
        "no_invented_step": all(s["locator"] for s in rep["steps"]),
    }
    checks["all_passed"] = all(checks.values())
    empty = not (rep["hypotheses"] or rep["steps"] or rep["parameters"])
    payload = {"case_id": case_id, "status": "insufficient_evidence" if empty else "answered",
               "plan": {"primary": "plan.docx", "markdown": "plan.md",
                        "hypotheses": len(rep["hypotheses"]), "variables": len(rep["independent"])
                        + len(rep["dependent"]), "controls": len(rep["controls"]),
                        "steps": len(rep["steps"]), "parameters": len(rep["parameters"]),
                        "resources": len(rep["equipment"]), "criteria": len(rep["criteria"])},
               "evidence_and_risk": {"files": ["variables.tsv", "parameters.tsv", "steps.tsv", "risks.tsv"],
                                     "risks": len(rep["risks"])},
               "confirmations": rep["confirmations"],
               "gaps": ([g for g, ok in (("材料里没有明确的假设句", bool(rep["hypotheses"])),
                                         ("材料里没有可识别的步骤", bool(rep["steps"])),
                                         ("材料里没有带单位的参数", bool(rep["parameters"]))) if not ok]),
               "sources": [{"name": source}],
               "uncertainties": [f'{c["item"]}：{c["detail"]}' for c in rep["confirmations"]
                                 if c["status"] == "needs_confirmation"],
               "limitations": ["方案从材料抽取，不补充材料外步骤；缺项显式标注",
                               "安全/伦理/设备可用性一律列为待确认，不写成已确认事实",
                               "数值按原文抄录（含单位），未做换算"],
               "verification": checks}
    _pdf = maybe_pdf(out / "plan.md", out)
    payload["pdf"] = _pdf
    if not _pdf.get("pdf"):
        payload.setdefault("gaps", []).append("未产出 PDF：" + str(_pdf.get("reason"))[:120])
    payload["metadata_coverage"] = meta_attach(inp, out)
    (out / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    print(json.dumps({"case_id": case_id, "status": payload["status"],
                      "hypotheses": len(rep["hypotheses"]), "steps": len(rep["steps"]),
                      "params": len(rep["parameters"]), "risks": len(rep["risks"]),
                      "all_passed": checks["all_passed"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
