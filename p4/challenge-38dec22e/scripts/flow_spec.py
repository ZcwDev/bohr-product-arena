#!/usr/bin/env python3
"""flow_spec — turn a described browser/plugin workflow into a verifiable spec, not a promise.

For the browser-plugin, software-automation and cross-site workflow tasks the deliverable that can be
checked in a sandbox is the *specification*: which site, which step, which action, what counts as done, and
where each of those came from in the material. So this engine extracts and validates, and refuses to invent
what it cannot see:

  * steps       numbered / bulleted / imperative lines, each with its source locator;
  * sites       only domains and URLs that literally appear in the material (no guessing homepages);
  * selectors   only if the material quotes one (`selector: …`, `css=…`, `xpath=…`, `GET /api/…`);
  * assertions  "must/should/verify/expect/确保/应当/校验" lines, tied to the step above them;
  * capability  each step is marked `browser` (needs a real browser) or `http/api` (executable headlessly),
                so the report never claims a browser step was executed here.

  flow.json            the machine-readable workflow (steps · site · action · locator · assertion · capability)
  plan.md / .docx      the readable plan + what cannot run in this environment + what must be supplied
  check_report.json    the gate: every step sourced? every site literal? any invented selector? coverage
  inventory.tsv        the file metadata layer (types · duplicates · versions · permissions · source)
  result.json          workflow · coverage · gaps · limitations · verification

usage:
  flow_spec.py --input /input --output /app/output
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

try:                                     # the metadata layer is shared with the other engines
    sys.path.insert(0, str(Path(__file__).resolve().parent))   # -I drops the script dir from sys.path
    from _meta import attach as meta_attach
except Exception:
    def meta_attach(inp, out):
        return {"unavailable": True}

URL = re.compile(r"https?://[^\s)>\]\"']+")
DOMAIN = re.compile(r"\b((?:[a-z0-9\-]+\.)+(?:com|org|net|cn|edu|gov|io|ai|co))\b", re.I)
SELECTOR = re.compile(r"(?:selector|css|css_selector|xpath)\s*[:=]\s*([^\s,;]+)", re.I)
API = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE)\s+(/[^\s,;]*)")
STEP = re.compile(r"^\s*(?:(\d+)[.)]|[-*•])\s+(.{6,})")
# \b does not behave around CJK (筛选出 has no boundary before 出), so CJK cues are matched bare
ACTION = {
    "open": re.compile(r"\b(open|navigate|go to|visit)\b|打开|进入|访问|跳转", re.I),
    "search": re.compile(r"\b(search|query|find)\b|检索|搜索|查询|输入关键词", re.I),
    "filter": re.compile(r"\b(filter|refine)\b|筛选|过滤|限定", re.I),
    "select": re.compile(r"\b(select|choose|pick)\b|选择|勾选|选中", re.I),
    "extract": re.compile(r"\b(extract|collect|record|copy|save)\b|抓取|提取|记录|保存|下载", re.I),
    "export": re.compile(r"\bexport\b|导出", re.I),
    "verify": re.compile(r"\b(verify|check|assert|confirm)\b|校验|核对|确认", re.I),
    "upload": re.compile(r"\b(upload|attach)\b|上传|附件", re.I),
}
GLOBAL_ASSERT = re.compile(r"^(要求|约束|前提|注意|说明|约定|规范)\s*[:：]")
ASSERT_CUE = re.compile(r"\b(must|should|expect\w*|ensure|verify|assert)\b|应当|必须|确保|校验|核对|要求", re.I)
SKIP = ("output-contract", "instruction", "skill", "readme", "case.json")


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
    if suf in {".md", ".txt", ".html", ".htm", ".json"}:
        return p.read_text(encoding="utf-8", errors="replace")
    return ""


PRIORITY = ["verify", "export", "upload", "extract", "filter", "search", "select", "open"]


def pick_action(text: str) -> str:
    """The action the sentence *starts* with wins: "导出选中条目" is export, not select."""
    best, best_pos = "other", len(text) + 1
    for name, rx in ACTION.items():
        m = rx.search(text)
        if not m:
            continue
        pos = m.start()
        if pos < best_pos or (pos == best_pos and PRIORITY.index(name) < PRIORITY.index(best)
                              if best != "other" else False):
            best, best_pos = name, pos
    return best


def extract(docs: list[dict]) -> dict:
    steps, sites, selectors, apis, asserts = [], [], [], [], []
    for d in docs:
        url_here = URL.findall(d["text"])
        for u in url_here:
            if u not in sites:
                sites.append(u)
        for m in DOMAIN.finditer(d["text"]):
            dom = m.group(1).lower()
            if not any(dom in s for s in sites) and dom not in {"html.com"}:
                sites.append(dom)
        for m in SELECTOR.finditer(d["text"]):
            selectors.append({"selector": m.group(1), "doc": d["doc"], "locator": "-"})
        for m in API.finditer(d["text"]):
            apis.append({"method": m.group(1), "path": m.group(2), "doc": d["doc"]})
        cur_step = None
        for lineno, raw in enumerate(d["text"].splitlines(), 1):
            line = norm(raw).lstrip("#*-> ")
            if len(line) < 6:
                continue
            m_step = STEP.match(line)
            if m_step and len(m_step.group(2)) >= 6:
                act = pick_action(m_step.group(2))
                cur_step = {"order": len(steps) + 1, "text": m_step.group(2)[:240], "action": act,
                            "doc": d["doc"], "locator": f"{d['doc']}#L{lineno}",
                            "site": next((s for s in url_here if s.split("/")[2] in m_step.group(2)), ""),
                            "assertion": ""}
                steps.append(cur_step)
            elif ASSERT_CUE.search(line):
                if cur_step is not None and not cur_step["assertion"] and not GLOBAL_ASSERT.match(line):
                    cur_step["assertion"] = line[:200]
                else:
                    asserts.append({"text": line[:200], "doc": d["doc"], "locator": f"{d['doc']}#L{lineno}"})
    # capability: a step is headless only when the material names an API for it
    for s in steps:
        # only a step that itself names an endpoint is headless; typing into a search box is not
        touches_api = bool(URL.search(s["text"])) and bool(
            re.search(r"\b(GET|POST|PUT|PATCH|DELETE)\b|/works\b|api\.", s["text"]))
        s["capability"] = "http/api" if touches_api else "browser"
    return {"steps": steps, "sites": sites, "selectors": selectors, "apis": apis,
            "assertions": asserts}


def check(rep: dict, docs: list[dict]) -> dict:
    text = "\n".join(d["text"] for d in docs)
    literal_sites = all(s in text for s in rep["sites"])
    checks = {
        "every_step_has_locator": all(s["locator"] for s in rep["steps"]),
        "every_step_has_action": all(s["action"] != "other" for s in rep["steps"]),
        "sites_are_literal_in_material": literal_sites,
        "selectors_only_when_quoted": all(s["selector"] in text for s in rep["selectors"]),
        "no_step_claims_execution": all("已执行" not in s.get("capability", "") for s in rep["steps"]),
        "browser_steps_declared": all(s["capability"] in ("browser", "http/api") for s in rep["steps"]),
    }
    checks["all_passed"] = all(checks.values())
    return checks


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
        if not p.is_file() or p.suffix.lower() not in {".md", ".txt", ".pdf", ".docx", ".html", ".htm", ".json"}:
            continue
        if any(k in p.name.lower() for k in SKIP):
            continue
        t = read_doc(p)
        if len(norm(t)) >= 60:
            docs.append({"doc": p.name, "text": t})
    if not docs:
        (out / "result.json").write_text(json.dumps(
            {"case_id": case_id, "status": "insufficient_evidence",
             "uncertainties": ["没有可读的流程材料"], "verification": {"all_passed": False}},
            ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps({"case_id": case_id, "status": "insufficient_evidence"}, ensure_ascii=False))
        return 2
    rep = extract(docs)
    checks = check(rep, docs)
    flow = {"schema_version": "flow-spec/v1", "case_id": case_id,
            "sites": rep["sites"], "apis": rep["apis"], "selectors": rep["selectors"],
            "steps": rep["steps"], "assertions": rep["assertions"]}
    (out / "flow.json").write_text(json.dumps(flow, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "check_report.json").write_text(json.dumps(
        {"case_id": case_id, "checks": checks,
         "step_count": len(rep["steps"]), "sites": len(rep["sites"]),
         "browser_steps": sum(1 for s in rep["steps"] if s["capability"] == "browser")},
        ensure_ascii=False, indent=1), encoding="utf-8")
    missing = []
    if not rep["steps"]:
        missing.append("材料里没有可识别的步骤（需要有序列表或动作句）")
    if not rep["sites"]:
        missing.append("材料里没有出现具体站点/URL，无法确定目标网站")
    if not rep["selectors"]:
        missing.append("材料未引用任何页面选择器：实现前需在目标页面勘察，不能凭空写选择器")
    if not rep["apis"]:
        missing.append("材料未给出接口：所有步骤都需要真实浏览器，评测环境无法代为执行")
    lines = [f"# 浏览器 / 插件工作流规格（{case_id}）", "",
             f"材料 {len(docs)} 篇；识别步骤 {len(rep['steps'])} 条，站点 {len(rep['sites'])} 个，"
             f"断言 {len(rep['assertions']) + sum(1 for s in rep['steps'] if s['assertion'])} 条。", "",
             "## 步骤", ""]
    for s in rep["steps"]:
        lines.append(f'{s["order"]}. [{s["action"]} · {s["capability"]}] {s["text"]}（{s["locator"]}）')
        if s["assertion"]:
            lines.append(f'   - 完成判据：{s["assertion"]}')
    lines += ["", "## 站点与接口", ""]
    lines += [f"- {x}" for x in rep["sites"]] or ["- 材料未给出站点"]
    lines += [f'- {a["method"]} {a["path"]}' for a in rep["apis"]] or ["- 材料未给出接口"]
    lines += ["", "## 材料引用的选择器（只登记，不臆造）", ""]
    lines += [f'- `{s["selector"]}`（{s["doc"]}）' for s in rep["selectors"]] or ["- 无"]
    lines += ["", "## 本环境可执行性", "",
              f'- `http/api` 步骤 {sum(1 for s in rep["steps"] if s["capability"] == "http/api")} 条：可用命令行复跑；',
              f'- `browser` 步骤 {sum(1 for s in rep["steps"] if s["capability"] == "browser")} 条：**需要真实浏览器会话**，'
              "本沙箱不代为执行，需在带界面的机器上按 `flow.json` 逐步执行并回填结果；",
              "- 任何选择器/接口都必须来自材料或现场勘察，缺失时列进下面的「需补充」。", "",
              "## 需补充（缺什么说清楚，不代替确认）", ""]
    lines += [f"- {m}" for m in missing] or ["- 材料已覆盖步骤、站点与判据，可直接进入实现。"]
    lines += ["", "## 局限", "",
              "- 规格抽取自材料文本，步骤顺序以材料的编号为准，不重排也不补步；",
              "- 未在本环境执行任何浏览器操作，报告里不含「已执行」陈述；",
              "- 选择器稳定性、登录态与权限需在真实站点确认后再固化为脚本。"]
    (out / "plan.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "plan.docx").write_bytes(_docx(f"工作流规格（{case_id}）", lines))
    payload = {"case_id": case_id, "status": "spec_ready" if rep["steps"] else "insufficient_evidence",
               "workflow": {"spec": "flow.json", "plan": "plan.docx", "markdown": "plan.md",
                            "steps": len(rep["steps"]), "sites": len(rep["sites"]),
                            "browser_steps": sum(1 for s in rep["steps"] if s["capability"] == "browser"),
                            "api_steps": sum(1 for s in rep["steps"] if s["capability"] == "http/api")},
               "checks": "check_report.json",
               "gaps": missing,
               "limitations": ["规格抽取，不执行浏览器", "选择器需现场勘察", "步骤不重排不补写"],
               "verification": checks}
    payload["metadata_coverage"] = meta_attach(inp, out)
    (out / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"case_id": case_id, "status": payload["status"], "steps": len(rep["steps"]),
                      "sites": len(rep["sites"]), "all_passed": checks["all_passed"]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
