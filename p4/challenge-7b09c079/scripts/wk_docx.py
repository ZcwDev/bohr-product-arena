#!/usr/bin/env python3
"""Stdlib OOXML writer.

* build_docx(blocks, out, ...)   - markdown-like blocks -> .docx; editorial
  annotations are written as real Word tracked insertions (<w:ins>), so the author
  can Accept/Reject each one individually in Word / LibreOffice.
* patch_docx(src, out, inserts)  - insert tracked paragraphs *after* given body
  element indices of an existing .docx, string-level, so every other byte of
  document.xml and every other zip part (styles, numbering, footnotes, headers,
  footers, media, fields) is preserved.
* count_tracked(docx)            - number of <w:ins> paragraphs we authored.

python-docx is NOT required (it is optional in the evaluator image).
"""
from __future__ import annotations

import re
import zipfile
from datetime import datetime, timezone
from xml.sax.saxutils import escape

AUTHOR = "weekly-skill (editorial annotation)"


def _esc(s: str) -> str:
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", s)
    return escape(s, {'"': "&quot;"})


def _runs(text: str, rpr: str = "", tag: str = "w:t") -> str:
    """Inline **bold** / *italic* markdown -> runs."""
    out = []
    for tok in re.split(r"(\*\*[^*]+\*\*|(?<!\*)\*[^*\s][^*]*\*(?!\*))", text):
        if not tok:
            continue
        extra = ""
        if tok.startswith("**") and tok.endswith("**") and len(tok) > 4:
            tok, extra = tok[2:-2], "<w:b/>"
        elif tok.startswith("*") and tok.endswith("*") and len(tok) > 2:
            tok, extra = tok[1:-1], "<w:i/>"
        props = rpr + extra
        out.append(f'<w:r>{"<w:rPr>" + props + "</w:rPr>" if props else ""}'
                   f'<{tag} xml:space="preserve">{_esc(tok)}</{tag}></w:r>')
    return "".join(out)


def ins_paragraph(text: str, rid: int, date: str, style: str = "EditorialAnnotation",
                  bookmark: str | None = None) -> str:
    """A whole paragraph that is a tracked insertion (paragraph mark + runs)."""
    bm = ""
    if bookmark:
        bm_name = re.sub(r"[^A-Za-z0-9_]", "_", bookmark)[:40]
        bm = (f'<w:bookmarkStart w:id="{rid}" w:name="{bm_name}"/>'
              f'<w:bookmarkEnd w:id="{rid}"/>')
    return (f'<w:p><w:pPr><w:pStyle w:val="{style}"/><w:rPr>'
            f'<w:ins w:id="{rid}" w:author="{_esc(AUTHOR)}" w:date="{date}"/></w:rPr></w:pPr>{bm}'
            f'<w:ins w:id="{rid + 1}" w:author="{_esc(AUTHOR)}" w:date="{date}">'
            f'{_runs(text)}</w:ins></w:p>')


STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Liberation Serif" w:hAnsi="Liberation Serif" w:eastAsia="Noto Serif CJK SC" w:cs="Liberation Serif"/><w:sz w:val="22"/><w:lang w:val="en-US" w:eastAsia="zh-CN"/></w:rPr></w:rPrDefault>
<w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="264" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:qFormat/><w:pPr><w:jc w:val="center"/><w:spacing w:after="240"/><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:sz w:val="34"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="240" w:after="120"/><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:sz w:val="30"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="200" w:after="100"/><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:b/><w:sz w:val="26"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="160" w:after="80"/><w:outlineLvl w:val="2"/></w:pPr><w:rPr><w:b/><w:i/><w:sz w:val="24"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Caption"><w:name w:val="caption"/><w:basedOn w:val="Normal"/><w:qFormat/><w:rPr><w:i/><w:sz w:val="20"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Equation"><w:name w:val="Equation"/><w:basedOn w:val="Normal"/><w:pPr><w:jc w:val="center"/></w:pPr><w:rPr><w:rFonts w:ascii="Liberation Mono" w:hAnsi="Liberation Mono"/><w:sz w:val="20"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/><w:basedOn w:val="Normal"/><w:pPr><w:ind w:left="567" w:hanging="283"/></w:pPr></w:style>
<w:style w:type="paragraph" w:styleId="Bibliography"><w:name w:val="Bibliography"/><w:basedOn w:val="Normal"/><w:pPr><w:ind w:left="567" w:hanging="567"/></w:pPr><w:rPr><w:sz w:val="20"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="EditorialAnnotation"><w:name w:val="Editorial Annotation"/><w:basedOn w:val="Normal"/><w:pPr><w:pBdr><w:left w:val="single" w:sz="18" w:space="6" w:color="1F6FB2"/></w:pBdr><w:shd w:val="clear" w:color="auto" w:fill="EAF2FB"/><w:ind w:left="284"/></w:pPr><w:rPr><w:sz w:val="20"/><w:color w:val="1F3B57"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Header"><w:name w:val="header"/><w:basedOn w:val="Normal"/><w:rPr><w:sz w:val="16"/><w:color w:val="666666"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Footer"><w:name w:val="footer"/><w:basedOn w:val="Normal"/><w:pPr><w:jc w:val="center"/></w:pPr><w:rPr><w:sz w:val="16"/></w:rPr></w:style>
<w:style w:type="table" w:styleId="TableGrid"><w:name w:val="Table Grid"/><w:tblPr><w:tblBorders><w:top w:val="single" w:sz="4" w:color="808080"/><w:left w:val="single" w:sz="4" w:color="808080"/><w:bottom w:val="single" w:sz="4" w:color="808080"/><w:right w:val="single" w:sz="4" w:color="808080"/><w:insideH w:val="single" w:sz="4" w:color="808080"/><w:insideV w:val="single" w:sz="4" w:color="808080"/></w:tblBorders></w:tblPr></w:style>
</w:styles>"""

W_DECL = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
          'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')


def _table_xml(text: str) -> str:
    rows = [r for r in text.splitlines() if r.strip().startswith("|")]
    rows = [r for r in rows if not re.match(r"^\|\s*:?-{2,}", r.strip())]
    out = ['<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblW w:w="0" w:type="auto"/></w:tblPr>']
    for ri, r in enumerate(rows):
        cells = [c.strip() for c in r.strip().strip("|").split("|")]
        out.append("<w:tr>")
        for c in cells:
            rpr = "<w:b/>" if ri == 0 else ""
            out.append(f'<w:tc><w:tcPr><w:tcW w:w="0" w:type="auto"/></w:tcPr><w:p><w:pPr><w:spacing w:after="0"/></w:pPr>'
                       f'{_runs(c, rpr)}</w:p></w:tc>')
        out.append("</w:tr>")
    out.append("</w:tbl>")
    return "".join(out)


def block_to_xml(b: dict) -> str:
    t = b["text"]
    s = t.lstrip()
    if b.get("is_table"):
        pre = [l for l in t.splitlines() if not l.strip().startswith("|")]
        xml = "".join(f"<w:p>{_runs(l)}</w:p>" for l in pre if l.strip())
        return xml + _table_xml(t)
    m = re.match(r"(#{1,6})\s+(.*)", s, re.S)
    if m:
        lvl = len(m.group(1))
        style = "Title" if lvl == 1 and b.get("first_heading") else f"Heading{min(lvl, 3)}"
        return f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr>{_runs(m.group(2).strip())}</w:p>'
    if b.get("is_equation"):
        return "".join(f'<w:p><w:pPr><w:pStyle w:val="Equation"/></w:pPr>{_runs(l.strip())}</w:p>'
                       for l in t.splitlines() if l.strip())
    style = ""
    if re.match(r"(\*\*)?(Table|Fig\.?|Figure|表|图)\s*\d", s):
        style = "Caption"
    elif b.get("is_reference_entry"):
        style = "Bibliography"
    lines = t.splitlines()
    if all(re.match(r"\s*([-*+]|\d+[.)])\s+", l) for l in lines if l.strip()) and len(lines) >= 1 and \
            re.match(r"\s*([-*+]|\d+[.)])\s+", lines[0]):
        return "".join(f'<w:p><w:pPr><w:pStyle w:val="ListParagraph"/></w:pPr>{_runs(l.strip())}</w:p>'
                       for l in lines if l.strip())
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    # keep soft line breaks inside a paragraph
    body = '<w:r><w:br/></w:r>'.join(_runs(l) for l in lines) if len(lines) > 1 and any(
        l.endswith("  ") or l.startswith("**") for l in lines) else _runs(" ".join(l.strip() for l in lines))
    return f"<w:p>{ppr}{body}</w:p>"


def _package(document_xml: str, header_text: str, footer_text: str) -> dict:
    hdr = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:hdr {W_DECL}>'
           f'<w:p><w:pPr><w:pStyle w:val="Header"/></w:pPr>{_runs(header_text)}</w:p></w:hdr>')
    ftr = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:ftr {W_DECL}>'
           f'<w:p><w:pPr><w:pStyle w:val="Footer"/></w:pPr>{_runs(footer_text + " — page ")}'
           '<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
           '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>1</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r>'
           '</w:p></w:ftr>')
    return {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        '<Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>'
        '<Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>'
        '<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>'
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        '</Types>',
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        '</Relationships>',
        "word/_rels/document.xml.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/>'
        '<Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>'
        '</Relationships>',
        "word/settings.xml": f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:settings {W_DECL}>'
        '<w:trackRevisions/><w:defaultTabStop w:val="720"/></w:settings>',
        "word/styles.xml": STYLES,
        "word/header1.xml": hdr,
        "word/footer1.xml": ftr,
        "word/document.xml": document_xml,
        "docProps/core.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>' + _esc(header_text[:200]) +
        '</dc:title><dc:creator>weekly-skill</dc:creator></cp:coreProperties>',
    }


def build_docx(blocks: list[dict], out_path, header_text: str = "", footer_text: str = "",
               inserts_after: dict | None = None, date: str | None = None) -> dict:
    """blocks: [{'text', is_heading, is_table, ...}]; inserts_after: {block_index: [(text, bookmark)]}.

    Returns {'tracked_insertions': n}."""
    date = date or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    inserts_after = inserts_after or {}
    parts, rid, n_ins = [], 9000, 0
    first_heading_seen = False
    for i, b in enumerate(blocks):
        bb = dict(b)
        if b.get("is_heading") and not first_heading_seen:
            bb["first_heading"] = b["text"].lstrip().startswith("# ")
            first_heading_seen = True
        parts.append(block_to_xml(bb))
        for text, bm in inserts_after.get(i, []):
            parts.append(ins_paragraph(text, rid, date, bookmark=bm))
            rid += 2
            n_ins += 1
    sect = ('<w:sectPr><w:headerReference w:type="default" r:id="rId3"/><w:footerReference w:type="default" r:id="rId4"/>'
            '<w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1304" w:right="1247" w:bottom="1304" w:left="1247" '
            'w:header="709" w:footer="709" w:gutter="0"/></w:sectPr>')
    doc = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {W_DECL}><w:body>'
           + "".join(parts) + sect + "</w:body></w:document>")
    pkg = _package(doc, header_text, footer_text)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", pkg.pop("[Content_Types].xml"))
        for k, v in pkg.items():
            zf.writestr(k, v)
    return {"tracked_insertions": n_ins}


_TOP = re.compile(r"<(/?)(w:p|w:tbl|w:sdt|w:body)\b([^>]*?)(/?)>")


def body_element_spans(xml: str) -> list[tuple[int, int]]:
    """(start, end) spans of top-level <w:p>/<w:tbl> inside <w:body> (sdt descended into like ET does not)."""
    spans, depth, start, in_body = [], 0, None, False
    for m in _TOP.finditer(xml):
        closing, name, selfclose = m.group(1), m.group(2), m.group(4)
        if name == "w:body":
            in_body = not closing
            continue
        if not in_body:
            continue
        if selfclose:
            if depth == 0 and name in ("w:p", "w:tbl"):
                spans.append((m.start(), m.end()))
            continue
        if not closing:
            if depth == 0:
                start = (m.start(), name)
            depth += 1
        else:
            depth -= 1
            if depth == 0 and start and start[1] in ("w:p", "w:tbl") and name == start[1]:
                spans.append((start[0], m.end()))
    return spans


def patch_docx(src, out_path, inserts_after: dict, date: str | None = None) -> dict:
    """inserts_after: {para_index(1-based, as wk_io.docx_paragraphs): [(text, bookmark)]}."""
    date = date or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with zipfile.ZipFile(src) as zin:
        xml = zin.read("word/document.xml").decode("utf-8")
        spans = body_element_spans(xml)
        rid, n = 9000, 0
        # walk backwards so offsets stay valid
        for idx in sorted(inserts_after, reverse=True):
            if idx < 1 or idx > len(spans):
                raise IndexError(f"body element {idx} not found (have {len(spans)})")
            end = spans[idx - 1][1]
            frag = "".join(ins_paragraph(t, rid + 2 * k, date, bookmark=bm)
                           for k, (t, bm) in enumerate(inserts_after[idx]))
            rid += 2 * len(inserts_after[idx]) + 2
            n += len(inserts_after[idx])
            xml = xml[:end] + frag + xml[end:]
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = xml.encode("utf-8") if item.filename == "word/document.xml" else zin.read(item.filename)
                zout.writestr(item, data)
    return {"tracked_insertions": n}


def count_tracked(path) -> int:
    with zipfile.ZipFile(path) as zf:
        xml = zf.read("word/document.xml").decode("utf-8", "replace")
    # run-level <w:ins ...> (not the self-closing paragraph-mark one) authored by this Skill
    return len(re.findall(r'<w:ins w:id="\d+" w:author="%s" w:date="[^"]*">' % re.escape(_esc(AUTHOR)), xml))
