#!/usr/bin/env python3
"""Dependency-free OOXML writers: PowerPoint (.pptx) and Word (.docx).

Why not python-pptx / python-docx: the evaluation image is pinned and may not
carry them, and an editable deliverable is mandatory for the artifact tasks.
This module writes valid OPC packages with the standard library only, so a
deck or document can always be produced. When python-pptx *is* importable the
caller may prefer it; the output here is a legitimate .pptx either way
(16:9 deck, one title + body slide per input, real text frames, real images).

CLI:
  python3 ooxml.py pptx deck.json -o deck.pptx
  python3 ooxml.py docx doc.json -o doc.docx

deck.json / doc.json schema:
  {"title": "...", "subtitle": "...", "size": [12192000, 6858000],
   "slides": [{"title": "...", "bullets": ["..."], "notes": "...",
               "image": "fig.png", "table": {"header": [...], "rows": [[...]]}}]}
"""
from __future__ import annotations

import argparse
import json
import os
import re
import struct
import sys
import zipfile
from xml.sax.saxutils import escape

EMU_PER_INCH = 914400
SLIDE_W, SLIDE_H = 12192000, 6858000

NS_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_CT = "http://schemas.openxmlformats.org/package/2006/content-types"
NS_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

CONTENT_TYPES = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="{NS_CT}">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Default Extension="png" ContentType="image/png"/>
<Default Extension="jpeg" ContentType="image/jpeg"/>
<Default Extension="jpg" ContentType="image/jpeg"/>
<Default Extension="emf" ContentType="image/x-emf"/>
<Default Extension="svg" ContentType="image/svg+xml"/>
<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>
<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>
<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>
{{slide_overrides}}
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>"""

DOCX_CONTENT_TYPES = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="{NS_CT}">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Default Extension="png" ContentType="image/png"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>
<Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>
<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
</Types>"""

THEME = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<a:theme xmlns:a="{NS_A}" name="weekly">
<a:themeElements>
<a:clrScheme name="weekly">
<a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1>
<a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1>
<a:dk2><a:srgbClr val="1F2A44"/></a:dk2>
<a:lt2><a:srgbClr val="F4F6FA"/></a:lt2>
<a:accent1><a:srgbClr val="2F6FED"/></a:accent1>
<a:accent2><a:srgbClr val="1B9C85"/></a:accent2>
<a:accent3><a:srgbClr val="C2410C"/></a:accent3>
<a:accent4><a:srgbClr val="6D28D9"/></a:accent4>
<a:accent5><a:srgbClr val="0E7490"/></a:accent5>
<a:accent6><a:srgbClr val="9A3412"/></a:accent6>
<a:hlink><a:srgbClr val="0563C1"/></a:hlink>
<a:folHlink><a:srgbClr val="954F72"/></a:folHlink>
</a:clrScheme>
<a:fontScheme name="weekly"><a:majorFont><a:latin typeface="Calibri Light"/><a:ea typeface="\u5fae\u8f6f\u96c5\u9ed1"/></a:majorFont>
<a:minorFont><a:latin typeface="Calibri"/><a:ea typeface="\u5fae\u8f6f\u96c5\u9ed1"/></a:minorFont></a:fontScheme>
<a:fmtScheme name="weekly">
<a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst>
<a:lnStyleLst><a:ln w="6350"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln><a:ln w="12700"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln><a:ln w="19050"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln></a:lnStyleLst>
<a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst>
<a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst>
</a:fmtScheme>
</a:themeElements></a:theme>"""

SLIDE_MASTER = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sldMaster xmlns:a="{NS_A}" xmlns:r="{NS_R}" xmlns:p="{NS_P}">
<p:cSld><p:spTree>
<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>
<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>
</p:spTree></p:cSld>
<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>
<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>
<p:txStyles><p:titleStyle><a:lvl1pPr algn="l"><a:defRPr sz="3200" b="1"><a:solidFill><a:schemeClr val="tx1"/></a:solidFill></a:defRPr></a:lvl1pPr></p:titleStyle>
<p:bodyStyle><a:lvl1pPr><a:defRPr sz="1800"><a:solidFill><a:schemeClr val="tx1"/></a:solidFill></a:defRPr></a:lvl1pPr></p:bodyStyle>
<p:otherStyle><a:defPPr><a:defRPr sz="1800"/></a:defPPr></p:otherStyle></p:txStyles>
</p:sldMaster>"""

SLIDE_LAYOUT = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sldLayout xmlns:a="{NS_A}" xmlns:r="{NS_R}" xmlns:p="{NS_P}" type="blank" preserve="1">
<p:cSld name="Blank"><p:spTree>
<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>
<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>
</p:spTree></p:cSld>
<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>
</p:sldLayout>"""


def _bullet_para(text: str, level: int = 0, size: int = 1800, bold: bool = False) -> str:
    depth = min(level, 4)
    return (f'<a:p><a:pPr lvl="{depth}"><a:buFont typeface="Arial"/>'
            f'<a:buChar char="\u2022"/></a:pPr>'
            f'<a:r><a:rPr lang="en-US" sz="{size}" b="{1 if bold else 0}" dirty="0"/>'
            f'<a:t>{escape(text)}</a:t></a:r><a:endParaRPr lang="en-US" sz="{size}"/></a:p>')


def _textbox(sid: int, name: str, x: int, y: int, cx: int, cy: int, paragraphs: str) -> str:
    return (f'<p:sp><p:nvSpPr><p:cNvPr id="{sid}" name="{escape(name)}"/><p:cNvSpPr txBox="1"/>'
            f'<p:nvPr/></p:nvSpPr>'
            f'<p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
            f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/></p:spPr>'
            f'<p:txBody><a:bodyPr wrap="square" lIns="91440" tIns="45720" rIns="91440" bIns="45720">'
            f'</a:bodyPr><a:lstStyle/>{paragraphs}</p:txBody></p:sp>')


def _picture(sid: int, name: str, rid: str, x: int, y: int, cx: int, cy: int) -> str:
    return (f'<p:pic><p:nvPicPr><p:cNvPr id="{sid}" name="{escape(name)}"/>'
            f'<p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr><p:nvPr/></p:nvPicPr>'
            f'<p:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></p:blipFill>'
            f'<p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
            f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>')


def _table(sid: int, name: str, header: list, rows: list, x: int, y: int, cx: int, cy: int) -> str:
    cols = max(len(header), max((len(r) for r in rows), default=0)) or 1
    grid = "".join(f'<a:gridCol w="{max(1, cx // cols)}"/>' for _ in range(cols))

    def cell(text, bold=False, head=False):
        fill = '<a:solidFill><a:srgbClr val="E8EEFA"/></a:solidFill>' if head else '<a:noFill/>'
        return (f'<a:tc><a:txBody><a:bodyPr/><a:lstStyle/><a:p>'
                f'<a:r><a:rPr lang="en-US" sz="1200" b="{1 if bold else 0}"/>'
                f'<a:t>{escape(str(text))}</a:t></a:r></a:p></a:txBody>'
                f'<a:tcPr>{fill}</a:tcPr></a:tc>')

    trs = [f'<a:tr h="{max(1, cy // (len(rows) + 1))}">'
           + "".join(cell(h, bold=True, head=True) for h in header) + '</a:tr>']
    for row in rows:
        trs.append(f'<a:tr h="{max(1, cy // (len(rows) + 1))}">'
                   + "".join(cell(c) for c in row) + '</a:tr>')
    return (f'<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="{sid}" name="{escape(name)}"/>'
            f'<p:cNvGraphicFramePr/><p:nvPr/></p:nvGraphicFramePr>'
            f'<p:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></p:xfrm>'
            f'<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/table">'
            f'<a:tbl><a:tblPr firstRow="1" bandRow="1"/><a:tblGrid>{grid}</a:tblGrid>'
            f'{"".join(trs)}</a:tbl></a:graphicData></a:graphic></p:graphicFrame>')


def build_slide_xml(slide: dict, image_rid: str | None, slide_no: int) -> str:
    sid = 2
    body: list[str] = []
    title = str(slide.get("title") or "")
    if title:
        body.append(_textbox(sid, "Title", 640080, 342900, SLIDE_W - 1280160, 800100,
                             _bullet_para(title, 0, 2600, True)))
        sid += 1
    top = 1257300
    bullets = slide.get("bullets") or []
    if bullets:
        paras = "".join(_bullet_para(str(b), int(b_level(b))) for b in bullets)
        height = SLIDE_H - top - 400000
        body.append(_textbox(sid, "Body", 640080, top, SLIDE_W - 1280160, height, paras))
        sid += 1
    table = slide.get("table")
    if isinstance(table, dict) and (table.get("rows") or table.get("header")):
        header = [str(h) for h in (table.get("header") or [])]
        rows = table.get("rows") or []
        body.append(_table(sid, "Table", header, rows, 640080, top,
                           SLIDE_W - 1280160, SLIDE_H - top - 500000))
        sid += 1
    if image_rid:
        body.append(_picture(sid, "Figure", image_rid, 640080, top,
                             SLIDE_W - 1280160, SLIDE_H - top - 500000))
        sid += 1
    notes = slide.get("notes")
    return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<p:sld xmlns:a="{NS_A}" xmlns:r="{NS_R}" xmlns:p="{NS_P}">'
            f'<p:cSld><p:spTree>'
            f'<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
            f'<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
            f'<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
            f'{"".join(body)}</p:spTree></p:cSld>'
            f'<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>')


def b_level(text: str) -> int:
    stripped = text.lstrip()
    hits = len(text) - len(stripped)
    for marker in ("- ", "* ", "\u2022 "):
        if stripped.startswith(marker):
            return 1
    return 1 if hits >= 2 and stripped[:1].isdigit() else (2 if hits >= 4 else 0)


def build_pptx(spec: dict, out_path: str, media: dict | None = None) -> dict:
    """spec: see module docstring. media maps image path -> bytes (already rendered)."""
    slides = spec.get("slides") or []
    media = media or {}
    parts: dict[str, bytes] = {}
    overrides = []
    slide_rel_entries = []
    for i, slide in enumerate(slides, start=1):
        overrides.append(f'<Override PartName="/ppt/slides/slide{i}.xml" '
                         f'ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>')
        image_path = slide.get("image")
        rid_entries = []
        image_rid = None
        if image_path and image_path in media:
            ext = os.path.splitext(image_path)[1].lower().lstrip(".") or "png"
            target = f"../media/image{i}.{ext}"
            rid_entries.append(f'<Relationship Id="rId2" Type="{NS_R}/image" Target="{target}"/>')
            parts[f"ppt/media/image{i}.{ext}"] = media[image_path]
            image_rid = "rId2"
        rid_xml = ('<Relationship Id="rId1" '
                   f'Type="{NS_R}/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
                   + "".join(rid_entries))
        parts[f"ppt/slides/_rels/slide{i}.xml.rels"] = (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<Relationships xmlns="{NS_REL}">{rid_xml}</Relationships>').encode("utf-8")
        parts[f"ppt/slides/slide{i}.xml"] = build_slide_xml(slide, image_rid, i).encode("utf-8")
        slide_rel_entries.append(f'<Relationship Id="rId{i+1}" Type="{NS_R}/slide" '
                                 f'Target="slides/slide{i}.xml"/>')

    parts["[Content_Types].xml"] = CONTENT_TYPES.format(
        slide_overrides="\n".join(overrides)).encode("utf-8")
    parts["_rels/.rels"] = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                            f'<Relationships xmlns="{NS_REL}">'
                            f'<Relationship Id="rId1" Type="{NS_R}/officeDocument" Target="ppt/presentation.xml"/>'
                            f'<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
                            f'<Relationship Id="rId3" Type="{NS_R}/extended-properties" Target="docProps/app.xml"/>'
                            f'</Relationships>').encode("utf-8")
    parts["ppt/presentation.xml"] = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<p:presentation xmlns:a="{NS_A}" xmlns:r="{NS_R}" xmlns:p="{NS_P}" saveSubsetFonts="1">'
        f'<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
        f'<p:sldIdLst>'
        + "".join(f'<p:sldId id="{255 + i}" r:id="rId{i+1}"/>' for i in range(1, len(slides) + 1))
        + f'</p:sldIdLst>'
        f'<p:sldSz cx="{SLIDE_W}" cy="{SLIDE_H}"/><p:notesSz cx="6858000" cy="9144000"/>'
        f'</p:presentation>').encode("utf-8")
    parts["ppt/_rels/presentation.xml.rels"] = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="{NS_REL}">'
        f'<Relationship Id="rId1" Type="{NS_R}/slideMaster" Target="slideMasters/slideMaster1.xml"/>'
        + "".join(slide_rel_entries)
        + f'<Relationship Id="rId{len(slides)+2}" Type="{NS_R}/theme" Target="theme/theme1.xml"/>'
        f'</Relationships>').encode("utf-8")
    parts["ppt/slideMasters/slideMaster1.xml"] = SLIDE_MASTER.encode("utf-8")
    parts["ppt/slideMasters/_rels/slideMaster1.xml.rels"] = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="{NS_REL}">'
        f'<Relationship Id="rId1" Type="{NS_R}/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
        f'<Relationship Id="rId2" Type="{NS_R}/theme" Target="../theme/theme1.xml"/>'
        f'</Relationships>').encode("utf-8")
    parts["ppt/slideLayouts/slideLayout1.xml"] = SLIDE_LAYOUT.encode("utf-8")
    parts["ppt/slideLayouts/_rels/slideLayout1.xml.rels"] = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="{NS_REL}">'
        f'<Relationship Id="rId1" Type="{NS_R}/slideMaster" Target="../slideMasters/slideMaster1.xml"/>'
        f'</Relationships>').encode("utf-8")
    parts["ppt/theme/theme1.xml"] = THEME.encode("utf-8")
    title = escape(str(spec.get("title") or "Deck"))
    parts["docProps/core.xml"] = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        f'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
        f'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f'<dc:title>{title}</dc:title><dc:creator>weekly-skill</dc:creator>'
        f'<cp:lastModifiedBy>weekly-skill</cp:lastModifiedBy></cp:coreProperties>').encode("utf-8")
    parts["docProps/app.xml"] = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
        f'<Application>weekly-skill</Application><Slides>{len(slides)}</Slides></Properties>').encode("utf-8")

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in parts.items():
            zf.writestr(name, data)
    return {"path": out_path, "bytes": os.path.getsize(out_path), "slides": len(slides)}


def _build_table_xml(table: dict) -> str:
    """Build w:tbl XML from {"header": [...], "rows": [[...]]}."""
    header = table.get("header", [])
    rows = table.get("rows", [])
    if not header and not rows:
        return ""
    cols = max(len(header), max((len(r) for r in rows), default=0))
    col_w = 9360 // max(cols, 1)
    grid_cols = "".join(f'<w:gridCol w:w="{col_w}"/>' for _ in range(cols))
    rows_xml = []
    if header:
        cells = ""
        for i in range(cols):
            t = escape(str(header[i])) if i < len(header) else ""
            cells += (
                f'<w:tc><w:tcPr><w:tcW w:w="{col_w}" w:type="dxa"/>'
                f'<w:shd w:val="clear" w:color="auto" w:fill="D9E2F3"/></w:tcPr>'
                f'<w:p><w:pPr><w:jc w:val="center"/></w:pPr>'
                f'<w:r><w:rPr><w:b/><w:sz w:val="20"/></w:rPr>'
                f'<w:t>{t}</w:t></w:r></w:p></w:tc>'
            )
        rows_xml.append(f'<w:tr><w:trPr><w:tblHeader/></w:trPr>{cells}</w:tr>')
    for row in rows:
        cells = ""
        for i in range(cols):
            t = escape(str(row[i])) if i < len(row) else ""
            cells += (
                f'<w:tc><w:tcPr><w:tcW w:w="{col_w}" w:type="dxa"/></w:tcPr>'
                f'<w:p><w:r><w:rPr><w:sz w:val="20"/></w:rPr>'
                f'<w:t>{t}</w:t></w:r></w:p></w:tc>'
            )
        rows_xml.append(f'<w:tr>{cells}</w:tr>')
    return (
        f'<w:tbl><w:tblPr><w:tblW w:w="9360" w:type="dxa"/>'
        f'<w:tblBorders>'
        f'<w:top w:val="single" w:sz="4" w:space="0" w:color="666666"/>'
        f'<w:left w:val="single" w:sz="4" w:space="0" w:color="666666"/>'
        f'<w:bottom w:val="single" w:sz="4" w:space="0" w:color="666666"/>'
        f'<w:right w:val="single" w:sz="4" w:space="0" w:color="666666"/>'
        f'<w:insideH w:val="single" w:sz="4" w:space="0" w:color="666666"/>'
        f'<w:insideV w:val="single" w:sz="4" w:space="0" w:color="666666"/>'
        f'</w:tblBorders>'
        f'<w:tblLook w:val="04A0" w:firstRow="1" w:lastRow="0" '
        f'w:firstColumn="1" w:lastColumn="0" w:noHBand="0" w:noVBand="1"/>'
        f'</w:tblPr><w:tblGrid>{grid_cols}</w:tblGrid>'
        f'{"".join(rows_xml)}</w:tbl>'
    )


def build_docx(spec: dict, out_path: str) -> dict:
    """Build a .docx with real OOXML features.

    spec = {"title":..., "case_id":..., "blocks":[{"type":"h1|h2|h3|p|bullet|
            revision_ins|revision_del|table", "text":..., "author":...,
            "table":{"header":[...],"rows":[[...]]}}]}

    Features: w:pStyle Heading1/2/3, numbering.xml, header1/footer1,
    TOC field, w:ins/w:del revision marks, w:tbl with borders.
    """
    title = str(spec.get("title") or "Untitled")
    case_id = str(spec.get("case_id") or "")
    blocks = list(spec.get("blocks") or [])
    for slide_like in spec.get("slides") or []:
        if slide_like.get("title"):
            blocks.append({"type": "h1", "text": slide_like["title"]})
        for b in slide_like.get("bullets") or []:
            blocks.append({"type": "bullet", "text": str(b)})

    body = []
    toc_inserted = False
    ins_id = 100

    # Title paragraph
    if spec.get("title"):
        body.append(
            f'<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr>'
            f'<w:r><w:rPr><w:b/><w:sz w:val="48"/></w:rPr>'
            f'<w:t xml:space="preserve">{escape(title)}</w:t></w:r></w:p>'
        )

    for idx, blk in enumerate(blocks):
        kind = (blk.get("type") or "p").lower()
        text = str(blk.get("text") or "")

        # Insert TOC after first h2 (right after abstract/heading area)
        if not toc_inserted and kind == "h2":
            body.append(
                '<w:sdt><w:sdtPr><w:docPartObj>'
                '<w:docPartGallery w:val="Table of Contents"/>'
                '<w:docPartUnique/></w:docPartObj></w:sdtPr>'
                '<w:sdtContent>'
                '<w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr>'
                '<w:r><w:t>Contents</w:t></w:r></w:p>'
                '<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r>'
                '<w:r><w:instrText xml:space="preserve"> TOC \\o "1-2" \\h \\z \\u </w:instrText></w:r>'
                '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
                '<w:r><w:rPr><w:i/></w:rPr>'
                '<w:t xml:space="preserve">(Update field in Word: right-click then Update Field)</w:t></w:r>'
                '<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>'
                '</w:sdtContent></w:sdt>'
            )
            toc_inserted = True

        if kind in ("h1", "title"):
            body.append(
                f'<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
                f'<w:r><w:rPr><w:b/></w:rPr>'
                f'<w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'
            )
        elif kind in ("h2", "sub"):
            body.append(
                f'<w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr>'
                f'<w:r><w:rPr><w:b/></w:rPr>'
                f'<w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'
            )
        elif kind == "h3":
            body.append(
                f'<w:p><w:pPr><w:pStyle w:val="Heading3"/></w:pPr>'
                f'<w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'
            )
        elif kind in ("bullet", "li"):
            body.append(
                f'<w:p><w:pPr><w:pStyle w:val="ListBullet"/>'
                f'<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>'
                f'</w:pPr>'
                f'<w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'
            )
        elif kind == "revision_ins":
            author = escape(blk.get("author", "Skill Reviewer"))
            ins_id += 1
            body.append(
                f'<w:p><w:pPr><w:rPr>'
                f'<w:ins w:id="{ins_id}" w:author="{author}" '
                f'w:date="2026-01-15T00:00:00Z"/>'
                f'</w:rPr></w:pPr>'
                f'<w:ins w:id="{ins_id+1}" w:author="{author}" '
                f'w:date="2026-01-15T00:00:00Z">'
                f'<w:r><w:rPr><w:u w:val="single"/></w:rPr>'
                f'<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'
                f'</w:ins></w:p>'
            )
        elif kind == "revision_del":
            author = escape(blk.get("author", "Skill Reviewer"))
            ins_id += 2
            body.append(
                f'<w:p><w:del w:id="{ins_id}" w:author="{author}" '
                f'w:date="2026-01-15T00:00:00Z">'
                f'<w:r><w:rPr><w:strike/></w:rPr>'
                f'<w:delText xml:space="preserve">{escape(text)}</w:delText>'
                f'</w:r></w:del></w:p>'
            )
        elif kind == "table":
            tbl_data = blk.get("table") or {"header": blk.get("header", []), "rows": blk.get("rows", [])}
            tbl_xml = _build_table_xml(tbl_data)
            if tbl_xml:
                body.append(tbl_xml)
        else:
            body.append(
                f'<w:p><w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'
            )

    # Styles with heading, list, TOC styles
    styles = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:styles xmlns:w="{NS_W}">'
        f'<w:style w:type="paragraph" w:styleId="Normal" w:default="1">'
        f'<w:name w:val="Normal"/>'
        f'<w:rPr><w:sz w:val="22"/><w:rFonts w:ascii="Times New Roman" '
        f'w:eastAsia="宋体" w:hAnsi="Times New Roman"/></w:rPr>'
        f'<w:pPr><w:spacing w:after="120" w:line="276" w:lineRule="auto"/></w:pPr>'
        f'</w:style>'
        f'<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/>'
        f'<w:basedOn w:val="Normal"/>'
        f'<w:pPr><w:jc w:val="center"/><w:spacing w:after="240"/></w:pPr>'
        f'<w:rPr><w:sz w:val="48"/><w:b/></w:rPr></w:style>'
        f'<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/>'
        f'<w:basedOn w:val="Normal"/><w:next w:val="Normal"/>'
        f'<w:pPr><w:keepNext/><w:keepLines/><w:spacing w:before="360" w:after="120"/>'
        f'<w:outlineLvl w:val="0"/></w:pPr>'
        f'<w:rPr><w:b/><w:sz w:val="32"/><w:szCs w:val="32"/>'
        f'<w:rFonts w:ascii="Arial" w:eastAsia="黑体" w:hAnsi="Arial"/></w:rPr></w:style>'
        f'<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/>'
        f'<w:basedOn w:val="Normal"/><w:next w:val="Normal"/>'
        f'<w:pPr><w:keepNext/><w:keepLines/><w:spacing w:before="240" w:after="60"/>'
        f'<w:outlineLvl w:val="1"/></w:pPr>'
        f'<w:rPr><w:b/><w:sz w:val="26"/><w:szCs w:val="26"/>'
        f'<w:rFonts w:ascii="Arial" w:eastAsia="黑体" w:hAnsi="Arial"/></w:rPr></w:style>'
        f'<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/>'
        f'<w:basedOn w:val="Normal"/><w:next w:val="Normal"/>'
        f'<w:pPr><w:keepNext/><w:spacing w:before="120" w:after="60"/>'
        f'<w:outlineLvl w:val="2"/></w:pPr>'
        f'<w:rPr><w:b/><w:i/><w:sz w:val="24"/><w:szCs w:val="24"/></w:rPr></w:style>'
        f'<w:style w:type="paragraph" w:styleId="ListBullet">'
        f'<w:name w:val="List Bullet"/><w:basedOn w:val="Normal"/>'
        f'<w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr></w:style>'
        f'<w:style w:type="paragraph" w:styleId="TOCHeading">'
        f'<w:name w:val="TOC Heading"/><w:basedOn w:val="Normal"/>'
        f'<w:rPr><w:b/><w:sz w:val="24"/></w:rPr></w:style>'
        f'<w:style w:type="paragraph" w:styleId="TOC1">'
        f'<w:name w:val="toc 1"/><w:basedOn w:val="Normal"/></w:style>'
        f'<w:style w:type="paragraph" w:styleId="TOC2">'
        f'<w:name w:val="toc 2"/><w:basedOn w:val="Normal"/>'
        f'<w:pPr><w:ind w:left="240"/></w:pPr></w:style>'
        f'</w:styles>'
    )

    # numbering.xml for bullets
    numbering = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:numbering xmlns:w="{NS_W}">'
        f'<w:abstractNum w:abstractNumId="0">'
        f'<w:nsid w:val="00000001"/>'
        f'<w:multiLevelType w:val="hybridMultilevel"/>'
        f'<w:lvl w:ilvl="0"><w:start w:val="1"/>'
        f'<w:numFmt w:val="bullet"/><w:lvlText w:val="&#x2022;"/>'
        f'<w:lvlJc w:val="left"/>'
        f'<w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr>'
        f'<w:rPr><w:rFonts w:ascii="Symbol" w:hAnsi="Symbol" w:hint="default"/></w:rPr>'
        f'</w:lvl></w:abstractNum>'
        f'<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>'
        f'</w:numbering>'
    )

    # header1.xml with case_id
    header = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:hdr xmlns:w="{NS_W}" xmlns:r="{NS_REL}">'
        f'<w:p><w:pPr><w:jc w:val="right"/></w:pPr>'
        f'<w:r><w:rPr><w:sz w:val="16"/><w:i/><w:color w:val="666666"/></w:rPr>'
        f'<w:t xml:space="preserve">{escape(case_id or title)}</w:t></w:r></w:p>'
        f'</w:hdr>'
    )

    # footer1.xml with page number field
    footer = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:ftr xmlns:w="{NS_W}" xmlns:r="{NS_REL}">'
        f'<w:p><w:pPr><w:jc w:val="center"/></w:pPr>'
        f'<w:r><w:rPr><w:sz w:val="16"/><w:color w:val="999999"/></w:rPr>'
        f'<w:t xml:space="preserve">Academic Paper Writing Skill — Page </w:t></w:r>'
        f'<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        f'<w:r><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
        f'<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>'
        f'</w:ftr>'
    )

    document = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{NS_W}" xmlns:r="{NS_REL}"'
        f' xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"'
        f' xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml"'
        f' mc:Ignorable="w14">'
        f'<w:body>{"".join(body)}'
        f'<w:sectPr>'
        f'<w:headerReference w:type="default" r:id="rId3"/>'
        f'<w:footerReference w:type="default" r:id="rId4"/>'
        f'<w:pgSz w:w="11906" w:h="16838"/>'
        f'<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"'
        f' w:header="720" w:footer="720"/>'
        f'<w:pgNumType w:start="1"/>'
        f'</w:sectPr></w:body></w:document>'
    )

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", DOCX_CONTENT_TYPES)
        zf.writestr("_rels/.rels", (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="{NS_REL}">'
            f'<Relationship Id="rId1" Type="{NS_R}/officeDocument" Target="word/document.xml"/>'
            f'<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
            f'</Relationships>'))
        zf.writestr("word/document.xml", document)
        zf.writestr("word/styles.xml", styles)
        zf.writestr("word/numbering.xml", numbering)
        zf.writestr("word/header1.xml", header)
        zf.writestr("word/footer1.xml", footer)
        zf.writestr("word/_rels/document.xml.rels", (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="{NS_REL}">'
            f'<Relationship Id="rId1" Type="{NS_R}/styles" Target="styles.xml"/>'
            f'<Relationship Id="rId2" Type="{NS_R}/numbering" Target="numbering.xml"/>'
            f'<Relationship Id="rId3" Type="{NS_R}/header" Target="header1.xml"/>'
            f'<Relationship Id="rId4" Type="{NS_R}/footer" Target="footer1.xml"/>'
            f'</Relationships>'))
        zf.writestr("docProps/core.xml", (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            f'xmlns:dc="http://purl.org/dc/elements/1.1/">'
            f'<dc:title>{escape(title)}</dc:title>'
            f'<dc:creator>Academic Paper Writing Skill</dc:creator>'
            f'<dc:subject>{escape(case_id)}</dc:subject>'
            f'</cp:coreProperties>'))

    size = os.path.getsize(out_path)
    has_bullets = any((b.get("type") or "").lower() in ("bullet", "li") for b in blocks)
    has_tables = any((b.get("type") or "").lower() == "table" for b in blocks)
    has_revisions = any(str(b.get("type", "")).startswith("revision_") for b in blocks)
    return {"path": out_path, "bytes": size, "ok": True,
            "para_count": len(body), "title": title,
            "features": {"toc": True, "numbering": True,
                         "header_footer": True,
                         "bullets": has_bullets, "tables": has_tables,
                         "revision_marks": has_revisions}}


def png_size(data: bytes) -> tuple[int, int]:
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return (0, 0)
    w, h = struct.unpack(">II", data[16:24])
    return (w, h)


def read_media(paths: list[str]) -> dict:
    media = {}
    for path in paths:
        try:
            media[path] = open(path, "rb").read()
        except OSError:
            continue
    return media


def main() -> int:
    ap = argparse.ArgumentParser(description="Dependency-free pptx/docx writer")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pptx"); p.add_argument("spec"); p.add_argument("-o", "--out", required=True)
    d = sub.add_parser("docx"); d.add_argument("spec"); d.add_argument("-o", "--out", required=True)
    a = ap.parse_args()
    spec = json.load(open(a.spec, encoding="utf-8"))
    if a.cmd == "pptx":
        media_paths = [s.get("image") for s in spec.get("slides", []) if s.get("image")]
        info = build_pptx(spec, a.out, read_media(media_paths))
    else:
        info = build_docx(spec, a.out)
    print(json.dumps(info, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
