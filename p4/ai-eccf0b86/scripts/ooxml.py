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


def build_docx(spec: dict, out_path: str) -> dict:
    """spec: {"title":..., "blocks":[{"type":"h1|h2|p|bullet|table","text":...,"rows":...}]}"""
    blocks = spec.get("blocks") or []
    for slide_like in spec.get("slides") or []:
        if slide_like.get("title"):
            blocks.append({"type": "h1", "text": slide_like["title"]})
        for b in slide_like.get("bullets") or []:
            blocks.append({"type": "bullet", "text": str(b)})

    def para(text, style=None, bold=False, bullet=False, level=0):
        ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        if bullet:
            ppr = (f'<w:pPr><w:ind w:left="{720 + 360 * level}"/>'
                   f'<w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/></w:rPr></w:pPr>')
            text = "\u2022 " + text if level == 0 else "- " + text
        runs = f'<w:r><w:rPr><w:b w:val="{1 if bold else 0}"/></w:rPr><w:t xml:space="preserve">{escape(text)}</w:t></w:r>'
        return f"<w:p>{ppr}{runs}</w:p>"

    body = []
    if spec.get("title"):
        body.append(para(str(spec["title"]), style="Title", bold=True))
    for block in blocks:
        kind = (block.get("type") or "p").lower()
        text = str(block.get("text") or "")
        if kind in ("h1", "title"):
            body.append(para(text, style="Heading1", bold=True))
        elif kind in ("h2", "sub"):
            body.append(para(text, style="Heading2", bold=True))
        elif kind in ("bullet", "li"):
            body.append(para(text, bullet=True, level=int(block.get("level") or 0)))
        elif kind == "table":
            rows = block.get("rows") or []
            if rows:
                cells = "".join(
                    "<w:tr>" + "".join(
                        f'<w:tc><w:tcPr/><w:p><w:r><w:t xml:space="preserve">{escape(str(c))}</w:t></w:r></w:p></w:tc>'
                        for c in row) + "</w:tr>" for row in rows)
                body.append(f'<w:tbl><w:tblPr><w:tblBorders>'
                            f'<w:top w:val="single" w:sz="4"/><w:bottom w:val="single" w:sz="4"/>'
                            f'<w:insideH w:val="single" w:sz="4"/><w:insideV w:val="single" w:sz="4"/>'
                            f'</w:tblBorders></w:tblPr>{cells}</w:tbl>')
        else:
            body.append(para(text))

    styles = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              f'<w:styles xmlns:w="{NS_W}">'
              f'<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/>'
              f'<w:rPr><w:sz w:val="56"/><w:b/></w:rPr></w:style>'
              f'<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/>'
              f'<w:pPr><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:sz w:val="36"/><w:b/></w:rPr></w:style>'
              f'<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/>'
              f'<w:pPr><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:sz w:val="28"/><w:b/></w:rPr></w:style>'
              f'</w:styles>')
    document = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<w:document xmlns:w="{NS_W}"><w:body>{"".join(body)}'
                f'<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
                f'<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"/></w:sectPr>'
                f'</w:body></w:document>')
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", DOCX_CONTENT_TYPES)
        zf.writestr("_rels/.rels", (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="{NS_REL}">'
            f'<Relationship Id="rId1" Type="{NS_R}/officeDocument" Target="word/document.xml"/>'
            f'<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
            f'</Relationships>'))
        zf.writestr("word/document.xml", document)
        zf.writestr("word/styles.xml", styles)
        zf.writestr("word/_rels/document.xml.rels", (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="{NS_REL}">'
            f'<Relationship Id="rId1" Type="{NS_R}/styles" Target="styles.xml"/></Relationships>'))
        zf.writestr("docProps/core.xml", (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            f'xmlns:dc="http://purl.org/dc/elements/1.1/">'
            f'<dc:title>{escape(str(spec.get("title") or "Document"))}</dc:title>'
            f'<dc:creator>weekly-skill</dc:creator></cp:coreProperties>'))
    return {"path": out_path, "bytes": os.path.getsize(out_path)}


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
