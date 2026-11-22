#!/usr/bin/env python3
"""Build four editable teaching documents and audited offline previews.

The input is an already extracted result object.  This module does not infer
scientific findings, complete missing values, or manufacture graph edges.  It
keeps one document specification for both the Word writer and the fallback
renderer, making the degraded preview inspectable and editable as well.

CLI:
  python3 deliverables.py build --result result.json --outdir OUT --workdir WORK
API:
  build_deliverables(result, outdir, workdir) -> dict
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
import posixpath
import re
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import ooxml  # noqa: E402
import preview  # noqa: E402
import render  # noqa: E402

DOCUMENT_STEMS = (
    "人体代谢动力学教学讲义",
    "酶学性质探究助教评估面板",
    "免疫学知识点讲义",
    "免疫学教学应用建议卡",
)
NUMBER_TOKEN = re.compile(r"([+-]?(?:\d+(?:[.,:/-]\d+)*)(?:[eE][+-]?\d+)?%?)")
MISSING = "not_reported"
GROUP_LABELS = {
    "targets": "靶点 / 分子对象",
    "pathways": "通路",
    "cells": "细胞",
    "outcomes": "结局",
    "cross_conditions": "跨条件比较",
}


def _load_result(source):
    if isinstance(source, (str, os.PathLike)):
        with open(source, encoding="utf-8") as handle:
            return json.load(handle)
    return source


def _payload(document):
    if not isinstance(document, dict):
        return {}
    nested = document.get("result")
    if not isinstance(nested, dict):
        return dict(document)
    # Keep top-level limitations/uncertainties available while giving the
    # extracted inner result precedence over wrapper metadata.
    merged = {k: v for k, v in document.items() if k != "result"}
    merged.update(nested)
    return merged


def _is_missing(value):
    return value is None or value == "" or value == [] or value == {}


def _scalar(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _value(value, path):
    if _is_missing(value):
        return f"{MISSING}\n原因：字段 {path} 未提供或为空；未以 0、常识或推测补齐。"
    if isinstance(value, str) and value.strip().lower() in (MISSING, "null", "none", "n/a"):
        return f"{MISSING}\n原因：字段 {path} 明确报告为缺失，未提供进一步数据。"
    return _scalar(value)


def _flatten(value, prefix=""):
    if isinstance(value, dict):
        if not value:
            return [(prefix or "value", None)]
        rows = []
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            rows.extend(_flatten(item, path))
        return rows
    if isinstance(value, list):
        if not value:
            return [(prefix or "value", None)]
        rows = []
        for index, item in enumerate(value):
            path = f"{prefix}[{index}]" if prefix else f"[{index}]"
            rows.extend(_flatten(item, path))
        return rows
    return [(prefix or "value", value)]


def _entries(value):
    if isinstance(value, list):
        return list(value)
    if _is_missing(value):
        return []
    return [value]


def _collection_block(value, path):
    rows = [["No.", "字段路径", "内容"]]
    entries = _entries(value)
    if not entries:
        rows.append(["No. 1", path, _value(None, path)])
    else:
        for index, entry in enumerate(entries):
            for field, leaf in _flatten(entry):
                source_path = f"{path}[{index}].{field}" if field != "value" else f"{path}[{index}]"
                rows.append([f"No. {index + 1}", field, _value(leaf, source_path)])
    return {"type": "table", "rows": rows, "widths": [0.10, 0.31, 0.59], "font_size": 7.4}


def _field_terms(value):
    terms = []
    for entry in _entries(value):
        for field, _ in _flatten(entry):
            terms.extend(part for part in re.split(r"\[\d+\]|\.", field) if part and part != "value")
    return terms


def _base_spec(title, payload):
    return {
        "title": title,
        "blocks": [
            {"type": "p", "text": "使用边界：仅转写输入 result 已明确提供的内容。缺失字段逐项显示 not_reported 与原因；缺失不等于 0、阴性或不存在，本文不补造科研事实、数值、单位或因果关系。"},
        ],
        "validation_terms": [title, "not_reported"],
    }


def _limitations(payload):
    limitations = payload.get("limitations")
    if _is_missing(limitations):
        limitations = payload.get("uncertainties")
    return limitations


def _add_collection(spec, heading, value, path):
    spec["blocks"].extend([{"type": "h1", "text": heading},
                           {"type": "p", "text": f"来源字段：{path}；表中字段路径相对于该来源逐项展开。"},
                           _collection_block(value, path)])
    spec["validation_terms"].extend([heading, path])
    spec["validation_terms"].extend(_field_terms(value))


def _add_limitations(spec, payload, path="result.limitations"):
    _add_collection(spec, "局限与人工接管点", _limitations(payload), path)
    uncertainties = payload.get("uncertainties")
    if not _is_missing(payload.get("limitations")) and not _is_missing(uncertainties):
        _add_collection(spec, "其他不确定性", uncertainties, "result.uncertainties")


def _metabolism_spec(payload):
    spec = _base_spec(DOCUMENT_STEMS[0], payload)
    _add_collection(spec, "核心结论（一致性与证据已分流）", payload.get("metabolism_core"), "result.metabolism_core")
    _add_collection(spec, "补充细节（证据较弱或适用范围较窄）", payload.get("metabolism_trifles"), "result.metabolism_trifles")
    spec["blocks"].append({"type": "p", "text": "分流边界：不在交付阶段重新归类 metabolism_core / metabolism_supplements；补充细节不提升为核心结论。"})
    _add_limitations(spec, payload)
    return spec


IDENTITY_KEYS = {"id", "target", "target_id", "name", "enzyme", "enzyme_name", "protein", "label", "title"}
FEEDBACK_KEYS = {"critical_feedback", "critical_assessment", "criticism", "critique", "feedback", "助教反馈", "批评性反馈"}
DIMENSION_CONTAINER_KEYS = ("six_dimensions", "dimensions", "assessment_dimensions", "properties")


def _target_name(target, index):
    if isinstance(target, dict):
        for key in ("target", "enzyme", "name", "target_id", "id", "label"):
            if not _is_missing(target.get(key)) and not isinstance(target.get(key), (dict, list)):
                return _scalar(target[key])
    return f"记录 No. {index + 1}"


def _dimension_item(item, path, index):
    if isinstance(item, dict):
        name = next((item[key] for key in ("dimension", "name", "key", "aspect", "label")
                     if not _is_missing(item.get(key)) and not isinstance(item.get(key), (dict, list))), None)
        if name is not None:
            value_keys = [key for key in ("value", "content", "finding", "result", "description") if key in item]
            if value_keys:
                value = item[value_keys[0]]
                used = {value_keys[0], "dimension", "name", "key", "aspect", "label"}
                remainder = {key: val for key, val in item.items() if key not in used}
                if remainder:
                    value = {"value": value, **remainder}
            else:
                value = {key: val for key, val in item.items()
                         if key not in ("dimension", "name", "key", "aspect", "label")}
            return str(name), value, path
        return f"维度记录 No. {index + 1}", item, path
    return f"维度记录 No. {index + 1}", item, path


def _six_dimensions(target, path):
    if not isinstance(target, dict):
        return [], []
    for container_key in DIMENSION_CONTAINER_KEYS:
        if container_key not in target:
            continue
        value = target[container_key]
        dimensions = []
        if isinstance(value, dict):
            if not value:
                return [], []
            for key, item in value.items():
                dimensions.append((str(key), item, f"{path}.{container_key}.{key}"))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                dimensions.append(_dimension_item(item, f"{path}.{container_key}[{index}]", index))
        else:
            dimensions.append((container_key, value, f"{path}.{container_key}"))
        return dimensions, []
    excluded = IDENTITY_KEYS | FEEDBACK_KEYS | {"educational_applications", "prerequisites", "non_inferable"}
    dimensions = [(str(key), item, f"{path}.{key}") for key, item in target.items() if key not in excluded]
    return dimensions[:6], dimensions[6:]


def _enzyme_spec(payload):
    spec = _base_spec(DOCUMENT_STEMS[1], payload)
    spec["blocks"].append({"type": "h1", "text": "逐靶点六维覆盖"})
    spec["validation_terms"].append("逐靶点六维覆盖")
    targets = payload.get("targets")
    entries = _entries(targets)
    if not entries:
        spec["blocks"].append(_collection_block(None, "result.targets"))
    for index, target in enumerate(entries):
        path = f"result.targets[{index}]"
        heading = f"靶点 No. {index + 1}：{_target_name(target, index)}"
        spec["blocks"].append({"type": "h2", "text": heading})
        spec["validation_terms"].append(heading)
        dimensions, extras = _six_dimensions(target, path)
        rows = [["维度", "提取内容", "字段路径 / 证据边界"]]
        for position in range(6):
            if position < len(dimensions):
                name, value, value_path = dimensions[position]
                rows.append([name, _value(value, value_path), value_path])
                spec["validation_terms"].append(name)
            else:
                field_name = f"维度 No. {position + 1}（字段名 not_reported）"
                rows.append([field_name, _value(None, f"{path}.dimensions[{position}]"),
                             "输入未声明该维度名称或数据，未用常规酶学类目补位"])
        spec["blocks"].append({"type": "table", "rows": rows, "widths": [0.20, 0.58, 0.22], "font_size": 7.1})
        if extras:
            extra_value = {name: value for name, value, _ in extras}
            spec["blocks"].append({"type": "h2", "text": f"靶点 No. {index + 1}：六维之外原始字段"})
            spec["blocks"].append(_collection_block([extra_value], path))
        feedback = None
        feedback_path = f"{path}.critical_feedback"
        if isinstance(target, dict):
            for key in FEEDBACK_KEYS:
                if key in target:
                    feedback, feedback_path = target[key], f"{path}.{key}"
                    break
        spec["blocks"].append({"type": "h2", "text": f"靶点 No. {index + 1}：批评性反馈"})
        spec["validation_terms"].append("批评性反馈")
        spec["blocks"].append(_collection_block({"critical_feedback": feedback}, feedback_path.rsplit(".", 1)[0]))
        spec["blocks"].append({"type": "p", "text": "批评性阅读：缺项不能解释为阴性结果或数值 0；参数连同其单位、温度、pH、底物和测定条件核查。若限定条件未提供，保留 not_reported，不从单一靶点外推普适机制或教学效果。"})
    _add_limitations(spec, payload)
    return spec


def _evidence_group(payload, key):
    evidence = payload.get("evidence_objects")
    path = "result.evidence_objects"
    if isinstance(evidence, dict) and key in evidence:
        return evidence[key], f"{path}.{key}"
    if isinstance(evidence, list):
        aliases = {key, key.rstrip("s"), key.replace("_", " ")}
        selected = []
        for index, item in enumerate(evidence):
            if isinstance(item, dict):
                group = item.get("category", item.get("group", item.get("kind", item.get("type"))))
                if isinstance(group, str) and group.strip().lower() in aliases:
                    selected.append(item)
        return selected, f"{path}[*].{key}"
    return None, f"{path}.{key}"


def _immune_spec(payload):
    spec = _base_spec(DOCUMENT_STEMS[2], payload)
    for key, label in GROUP_LABELS.items():
        value, path = _evidence_group(payload, key)
        _add_collection(spec, label, value, path)
    evidence = payload.get("evidence_objects")
    if isinstance(evidence, dict) and "interpretation_completeness" in evidence:
        completeness = evidence["interpretation_completeness"]
        path = "result.evidence_objects.interpretation_completeness"
    else:
        completeness = payload.get("interpretation_completeness")
        path = "result.interpretation_completeness"
    _add_collection(spec, "interpretation_completeness（解释完整性）", completeness, path)
    spec["blocks"].append({"type": "p", "text": "解释边界：对象共现不自动形成机制边；跨条件条目不自动等同因果证据。知识关系图仅采用明确给出的关系记录。"})
    _add_limitations(spec, payload)
    return spec


def _first_declared(payload, aliases):
    for key in aliases:
        if key in payload:
            return payload[key], f"result.{key}"
    return None, f"result.{aliases[0]}"


def _application_text(item):
    if not isinstance(item, dict):
        return item
    for key in ("application", "recommendation", "suggestion", "activity", "teaching_use", "use", "description", "text"):
        if key in item:
            return item[key]
    return {key: value for key, value in item.items()
            if key not in ("evidence", "support", "direct_support", "source", "source_id", "location", "locator", "quote")}


def _application_support(item, path):
    if not isinstance(item, dict):
        return None, path
    for key in ("evidence", "direct_support", "support", "source", "citation", "source_id", "location", "locator", "quote"):
        if key in item:
            return item[key], f"{path}.{key}"
    if item.get("directly_supported") is True or item.get("supported") is True:
        return "源对象明确标记 directly_supported / supported = true；独立原文定位未另行提供。", path
    return None, f"{path}.evidence"


def _explicitly_unsupported(item):
    if not isinstance(item, dict):
        return False
    return any(item.get(key) is False for key in ("directly_supported", "supported", "education_supported"))


def _advice_spec(payload):
    spec = _base_spec(DOCUMENT_STEMS[3], payload)
    spec["blocks"].append({"type": "p", "text": "转交红线：本卡只转写输入已明确列出的教育应用、先决和不可推断内容。未附独立支持定位的应用不得升级为已验证教学干预；关系图孤立节点不表示无关系，图示与卡片均按 A4 正常字号分页，不以微型字或截图代替可编辑内容。"})
    applications, app_path = _first_declared(payload, (
        "educational_applications", "teaching_applications", "teaching_implications",
        "application_suggestions", "education_recommendations",
    ))
    spec["blocks"].append({"type": "h1", "text": "直接列明的教育应用"})
    spec["validation_terms"].append("直接列明的教育应用")
    rows = [["No.", "教育应用", "直接支持 / 定位"]]
    included = 0
    nested_prerequisites = []
    for index, item in enumerate(_entries(applications)):
        item_path = f"{app_path}[{index}]"
        if _explicitly_unsupported(item):
            continue
        included += 1
        support, support_path = _application_support(item, item_path)
        rows.append([f"No. {included}", _value(_application_text(item), item_path), _value(support, support_path)])
        if isinstance(item, dict) and "prerequisites" in item:
            nested_prerequisites.append({"application_no": included, "prerequisites": item["prerequisites"]})
    if included == 0:
        reason = (f"{app_path} 未提供或为空；未从科研结局反推课堂应用。" if not _entries(applications)
                  else f"{app_path} 中条目明确标记支持不足，未作为直接教育建议转交。")
        rows.append(["No. 1", MISSING, f"原因：{reason}"])
    spec["blocks"].append({"type": "table", "rows": rows, "widths": [0.09, 0.52, 0.39], "font_size": 7.3})
    prerequisites, prerequisite_path = _first_declared(payload, (
        "prerequisites", "teaching_prerequisites", "application_prerequisites", "required_prerequisites",
    ))
    if _is_missing(prerequisites) and nested_prerequisites:
        prerequisites, prerequisite_path = nested_prerequisites, f"{app_path}[*].prerequisites"
    _add_collection(spec, "实施先决（仅直接提供）", prerequisites, prerequisite_path)
    non_inferable, inference_path = _first_declared(payload, (
        "non_inferable", "not_inferable", "cannot_infer", "unsupported_inferences",
        "prohibited_inferences", "out_of_scope",
    ))
    _add_collection(spec, "不可推断 / 不得外推", non_inferable, inference_path)
    spec["blocks"].append({"type": "p", "text": "不可推断：不得把对象共现写成因果机制，不得把未报告数据写成 0 或无差异，不得把教育应用写成已证明提升学习成绩；若输入未明确给出相应直接证据，该项保持 not_reported。"})
    _add_limitations(spec, payload)
    return spec


def _graph_label(item):
    if isinstance(item, dict):
        for key in ("name", "label", "target", "pathway", "cell", "outcome", "condition", "id", "key", "description"):
            value = item.get(key)
            if not _is_missing(value) and not isinstance(value, (dict, list)):
                return _scalar(value)
    elif not isinstance(item, list):
        return _scalar(item)
    return json.dumps(item, ensure_ascii=False, separators=(",", ":"))


def _short_label(label, limit=52):
    text = " ".join(str(label).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _matrix_spec(payload, exception_notes):
    layers, nodes, aliases = [], [], {}

    def alias(value, node_id):
        if _is_missing(value) or isinstance(value, (dict, list)):
            return
        key = str(value)
        if key in aliases and aliases[key] != node_id:
            aliases[key] = None  # ambiguous aliases are never resolved silently
        else:
            aliases[key] = node_id

    for layer_index, (key, layer_label) in enumerate(GROUP_LABELS.items()):
        value, _ = _evidence_group(payload, key)
        entries = _entries(value)
        layer = []
        if not entries:
            node_id = f"{key}_not_reported"
            layer.append(node_id)
            nodes.append({"id": node_id, "label": f"{layer_label}\nnot_reported", "kind": "box",
                          "note": "该组未提供或为空"})
        else:
            visible = entries[:8]
            for index, item in enumerate(visible):
                node_id = f"{key}_{index + 1}"
                layer.append(node_id)
                label = _short_label(_graph_label(item))
                nodes.append({"id": node_id, "label": label, "kind": "process" if key == "pathways" else "box",
                              "note": f"{layer_label} No. {index + 1}"})
                alias(node_id, node_id)
                if isinstance(item, dict):
                    for alias_key in ("id", "key", "name", "label", "target", "pathway", "cell", "outcome", "condition"):
                        alias(item.get(alias_key), node_id)
                else:
                    alias(item, node_id)
            if len(entries) > len(visible):
                node_id = f"{key}_remaining"
                layer.append(node_id)
                nodes.append({"id": node_id, "label": f"其余 {len(entries) - len(visible)} 项",
                              "note": "完整内容见免疫学知识点讲义"})
        layers.append(layer)

    relation_records = []
    evidence = payload.get("evidence_objects")
    if isinstance(evidence, dict):
        for key in ("relations", "relationships", "edges"):
            if isinstance(evidence.get(key), list):
                relation_records.extend((f"result.evidence_objects.{key}[{i}]", edge)
                                        for i, edge in enumerate(evidence[key]))
        for index, item in enumerate(_entries(evidence.get("targets"))):
            if isinstance(item, dict) and isinstance(item.get("relations"), list):
                relation_records.extend((f"result.evidence_objects.targets[{index}].relations[{i}]", edge)
                                        for i, edge in enumerate(item["relations"]))

    def endpoint(edge, keys):
        for key in keys:
            value = edge.get(key)
            if isinstance(value, dict):
                value = next((value[k] for k in ("id", "name", "label", "key") if k in value), value)
            if not _is_missing(value) and not isinstance(value, (dict, list)):
                return str(value)
        return None

    edges = []
    for path, edge in relation_records:
        if not isinstance(edge, dict):
            exception_notes.append({"code": "GRAPH_EDGE_OMITTED", "path": path,
                                    "reason": "关系记录不是对象，无法核验端点；未绘制。"})
            continue
        source = endpoint(edge, ("from", "source", "subject"))
        target = endpoint(edge, ("to", "target", "object", "related_to"))
        source_id, target_id = aliases.get(source), aliases.get(target)
        if not source_id or not target_id or source_id == target_id:
            exception_notes.append({"code": "GRAPH_EDGE_OMITTED", "path": path,
                                    "source": source or MISSING, "target": target or MISSING,
                                    "reason": "端点缺失、存在歧义、指向同一节点或未匹配可见对象；未绘制。"})
            continue
        stated_kind = str(edge.get("type") or edge.get("relation") or edge.get("kind") or "association")
        kind = stated_kind if stated_kind in ("activation", "inhibition", "transport", "binding",
                                               "conversion", "association", "phosphorylation") else "association"
        edges.append({"id": f"relation_{len(edges) + 1}", "from": source_id, "to": target_id,
                      "type": kind, "label": _short_label(edge.get("label") or stated_kind, 30),
                      "source_path": path})
    max_row = max((len(layer) for layer in layers), default=1)
    return {
        "title": "教学知识关系图",
        "subtitle": "仅绘制 evidence_objects 明确给出的关系；孤立节点不表示无关系，未解析边不补画。",
        "layers": layers, "compartments": list(GROUP_LABELS.values()), "nodes": nodes, "edges": edges,
        "width": max(1180, 220 * max_row + 180), "node_width": 190, "node_height": 68, "layer_gap": 90,
        "legend": [], "edge_source_policy": "explicit_only",
    }


def _svg_text(x, y, text, size=14, anchor="middle", weight="normal", fill="#0F172A", css_font=""):
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-family="{escape(css_font)}" font-size="{size}" '
            f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}" dominant-baseline="middle">'
            f'{escape(str(text))}</text>')


def _matrix_svg(spec, font_css):
    width = int(spec["width"])
    node_w, node_h, gap = int(spec["node_width"]), int(spec["node_height"]), int(spec["layer_gap"])
    height = 150 + len(spec["layers"]) * (node_h + gap) + 45
    position = {}
    for li, layer in enumerate(spec["layers"]):
        count = max(1, len(layer))
        span = width - 180
        for index, node_id in enumerate(layer):
            x = width / 2 if count == 1 else 90 + span * (index + 0.5) / count
            position[node_id] = (x, 150 + li * (node_h + gap) + node_h / 2)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
           '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M 0 0 L 10 5 L 0 10 z" fill="#334155"/></marker><marker id="bar" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M 8 0 V 10" stroke="#334155" stroke-width="2"/></marker></defs>',
           f'<rect width="{width}" height="{height}" fill="#FFFFFF"/>',
           _svg_text(width / 2, 48, spec["title"], 24, weight="bold", css_font=font_css),
           _svg_text(width / 2, 83, spec["subtitle"], 13, fill="#475569", css_font=font_css)]
    for index, label in enumerate(spec["compartments"]):
        _, y = position[spec["layers"][index][0]]
        out.append(_svg_text(18, y - node_h / 2 - 15, label, 12, anchor="start", fill="#64748B", css_font=font_css))
    for edge in spec["edges"]:
        x1, y1 = position[edge["from"]]
        x2, y2 = position[edge["to"]]
        if abs(y2 - y1) >= abs(x2 - x1):
            start = (x1, y1 + (node_h / 2 if y2 >= y1 else -node_h / 2))
            end = (x2, y2 + (-node_h / 2 if y2 >= y1 else node_h / 2))
        else:
            start = (x1 + (node_w / 2 if x2 >= x1 else -node_w / 2), y1)
            end = (x2 + (-node_w / 2 if x2 >= x1 else node_w / 2), y2)
        kind = edge["type"]
        dash = ' stroke-dasharray="6 4"' if kind in ("association", "inhibition") else ""
        marker = ' marker-end="url(#arrow)"' if kind in ("activation", "transport", "conversion", "phosphorylation") else (' marker-end="url(#bar)"' if kind == "inhibition" else "")
        out.append(f'<path id="{escape(edge["id"])}" data-role="edge" data-from="{escape(edge["from"])}" data-to="{escape(edge["to"])}" data-type="{kind}" d="M {start[0]:.1f} {start[1]:.1f} L {end[0]:.1f} {end[1]:.1f}" fill="none" stroke="#334155" stroke-width="2"{dash}{marker}/>')
        mx, my = (start[0] + end[0]) / 2, (start[1] + end[1]) / 2
        out.append(f'<rect x="{mx - 48:.1f}" y="{my - 13:.1f}" width="96" height="26" rx="5" fill="#FFFFFF" opacity="0.93"/>')
        out.append(_svg_text(mx, my, edge["label"], 11, fill="#334155", css_font=font_css))
    for node in spec["nodes"]:
        cx, cy = position[node["id"]]
        x, y = cx - node_w / 2, cy - node_h / 2
        out.append(f'<g id="node-{escape(node["id"])}" data-role="node" data-kind="{escape(node.get("kind") or "box")}">')
        out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{node_w}" height="{node_h}" rx="10" fill="#F1F5F9" stroke="#2F6FED" stroke-width="1.8"/>')
        lines = str(node["label"]).splitlines()
        wrapped = []
        for line in lines:
            wrapped.extend(line[i:i + 20] for i in range(0, len(line), 20))
        wrapped = wrapped or [MISSING]
        for li, line in enumerate(wrapped):
            out.append(_svg_text(cx, cy + (li - (len(wrapped) - 1) / 2) * 16, line, 12, weight="bold", css_font=font_css))
        out.append('</g>')
        if node.get("note"):
            out.append(_svg_text(cx, cy + node_h / 2 + 14, node["note"], 10, fill="#64748B", css_font=font_css))
    out.append('</svg>')
    return "\n".join(out)


def _python_docx(spec, path):
    from docx import Document
    from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt

    document = Document()
    section = document.sections[0]
    section.page_width, section.page_height = Cm(21.0), Cm(29.7)
    section.left_margin = section.right_margin = Cm(2.0)
    section.top_margin = section.bottom_margin = Cm(2.0)
    fonts = spec["fonts"]

    def set_style(name, size, bold=False):
        style = document.styles[name]
        style.font.name = fonts["latin"]
        style.font.size = Pt(size)
        style.font.bold = bold
        style.element.rPr.rFonts.set(qn("w:eastAsia"), fonts["cjk"])
        style.element.rPr.rFonts.set(qn("w:ascii"), fonts["latin"])
        style.element.rPr.rFonts.set(qn("w:hAnsi"), fonts["latin"])

    set_style("Normal", 10)
    set_style("Title", 20, True)
    set_style("Heading 1", 15, True)
    set_style("Heading 2", 12, True)

    def add_text(paragraph, text, bold=False, mono=False, size=9):
        for piece in NUMBER_TOKEN.split(str(text)):
            if not piece:
                continue
            run = paragraph.add_run(piece)
            numeric = bool(NUMBER_TOKEN.fullmatch(piece))
            selected = fonts["mono"] if mono or numeric else fonts["latin"]
            run.font.name = selected
            run.font.size = Pt(size)
            run.bold = bold
            run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), fonts["cjk"])
            run._element.get_or_add_rPr().rFonts.set(qn("w:ascii"), selected)
            run._element.get_or_add_rPr().rFonts.set(qn("w:hAnsi"), selected)

    if spec.get("title"):
        add_text(document.add_paragraph(style="Title"), spec["title"], True, size=20)
    for block in spec.get("blocks") or []:
        kind = str(block.get("type") or "p").lower()
        if kind == "table":
            rows = block.get("rows") or []
            if not rows:
                continue
            columns = max(len(row) for row in rows)
            table = document.add_table(rows=0, cols=columns)
            table.alignment = WD_TABLE_ALIGNMENT.CENTER
            table.style = "Table Grid"
            table.autofit = True
            for ri, row in enumerate(rows):
                cells = table.add_row().cells
                for ci in range(columns):
                    cells[ci].vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
                    value = str(row[ci]) if ci < len(row) else ""
                    add_text(cells[ci].paragraphs[0], value, ri == 0,
                             size=float(block.get("font_size") or 7.4))
                    if ri == 0:
                        shading = OxmlElement("w:shd")
                        shading.set(qn("w:fill"), "E8EEFA")
                        cells[ci]._tc.get_or_add_tcPr().append(shading)
                if ri == 0:
                    repeat = OxmlElement("w:tblHeader")
                    repeat.set(qn("w:val"), "true")
                    table.rows[-1]._tr.get_or_add_trPr().append(repeat)
        elif kind == "page_break":
            document.add_page_break()
        else:
            style = "Heading 1" if kind in ("h1", "title") else ("Heading 2" if kind in ("h2", "sub") else "Normal")
            paragraph = document.add_paragraph(style=style)
            text = str(block.get("text") or "")
            if kind in ("bullet", "li"):
                text = "- " + text
            add_text(paragraph, text, kind in ("h1", "h2", "title"),
                     kind in ("code", "pre"), 20 if kind == "title" else (15 if kind == "h1" else (12 if kind == "h2" else 9.2)))
    document.save(path)


def _write_docx(spec, path, exception_notes):
    try:
        import docx  # noqa: F401
        _python_docx(spec, str(path))
        return "python-docx"
    except ImportError:
        note = {"code": "WRITER_FALLBACK", "severity": "warning",
                "engine": "ooxml_stdlib", "reason": "python-docx 不可用；使用标准库 OOXML，Word 正文与表格仍可编辑。"}
        if note not in exception_notes:
            exception_notes.append(note)
        ooxml.build_docx(spec, str(path))
        return "ooxml_stdlib"


def _validate_docx(path, spec):
    required = {"[Content_Types].xml", "_rels/.rels", "word/document.xml", "word/styles.xml",
                "word/_rels/document.xml.rels", "docProps/core.xml"}
    problems, xml_parts, relationship_missing = [], [], []
    with zipfile.ZipFile(path) as archive:
        corrupt = archive.testzip()
        if corrupt:
            problems.append(f"ZIP corruption: {corrupt}")
        names = set(archive.namelist())
        missing_parts = sorted(required - names)
        problems.extend(f"missing part: {part}" for part in missing_parts)
        for name in sorted(names):
            if name.endswith(".xml") or name.endswith(".rels"):
                try:
                    ET.fromstring(archive.read(name))
                    xml_parts.append(name)
                except ET.ParseError as exc:
                    problems.append(f"XML parse error {name}: {exc}")
        for name in sorted(part for part in names if part.endswith(".rels")):
            root = ET.fromstring(archive.read(name))
            owner = name[:-5].replace("_rels/", "")
            base = posixpath.dirname(owner)
            for relation in root:
                if relation.attrib.get("TargetMode") == "External":
                    continue
                target = relation.attrib.get("Target", "")
                resolved = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(base, target))
                if resolved and resolved not in names:
                    relationship_missing.append({"part": name, "target": resolved})
        document_root = ET.fromstring(archive.read("word/document.xml"))
        visible_text = "".join(document_root.itertext())
        document_xml = archive.read("word/document.xml").decode("utf-8")
        styles_xml = archive.read("word/styles.xml").decode("utf-8")
    expected = []
    for term in spec.get("validation_terms") or []:
        if str(term) and str(term) not in expected:
            expected.append(str(term))
    missing_terms = [term for term in expected if term not in visible_text]
    problems.extend(f"missing visible field/title: {term}" for term in missing_terms)
    unicode_sequence = re.findall(r"[\u2460-\u2473\u3251-\u32bf]", visible_text)
    if unicode_sequence:
        problems.append("Unicode circled/enclosed sequence markers found; use No.")
    for role, font in spec.get("fonts", {}).items():
        if str(font) not in document_xml + styles_xml:
            problems.append(f"font role not serialized: {role}={font}")
    package_complete = not missing_parts and not relationship_missing and not corrupt
    return {"path": str(path), "ok": not problems, "package_complete": package_complete,
            "bytes": os.path.getsize(path), "xml_parts_read": xml_parts,
            "required_parts_present": not missing_parts, "missing_relationship_targets": relationship_missing,
            "titles_and_fields_present": not missing_terms, "missing_terms": missing_terms,
            "sequence_marker_policy": "No.", "font_roles_present": not any("font role" in p for p in problems),
            "problems": problems}


def _image_is_nonempty(image):
    gray = image.convert("L")
    gray.thumbnail((180, 240))
    return gray.getextrema()[0] < 245


def _preview_check(pdf_path, png_paths, require_a4):
    from PIL import Image
    import struct

    pdf_path = str(pdf_path)
    data = Path(pdf_path).read_bytes() if Path(pdf_path).is_file() else b""
    pdf_magic = data.startswith(b"%PDF-")
    pages, pdf_engine = [], "structure_scan"
    fitz_ok = False
    try:
        import fitz
        with fitz.open(pdf_path) as document:
            fitz_ok = True
            pdf_engine = "pymupdf"
            for page in document:
                pixmap = page.get_pixmap(matrix=fitz.Matrix(0.45, 0.45), alpha=False)
                image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
                pages.append({"size_pt": [round(page.rect.width, 2), round(page.rect.height, 2)],
                              "nonempty_content": _image_is_nonempty(image)})
    except Exception:
        page_count = len(re.findall(rb"/Type\s*/Page(?!s)", data))
        media = re.search(rb"/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)\s*\]", data)
        size = [float(media.group(1)), float(media.group(2))] if media else [0.0, 0.0]
        pages = [{"size_pt": size, "nonempty_content": len(data) > 500} for _ in range(page_count)]
    pngs = []
    for path in png_paths:
        raw = Path(path).read_bytes()
        magic = raw.startswith(b"\x89PNG\r\n\x1a\n") and len(raw) > 33
        width, height = struct.unpack(">II", raw[16:24]) if magic else (0, 0)
        with Image.open(path) as image:
            image.load()
            nonempty = _image_is_nonempty(image)
        a4_shape = abs(width / max(1, height) - 1 / math.sqrt(2)) < 0.006
        pngs.append({"path": str(path), "magic": magic, "bytes": len(raw),
                     "dimensions_px": [width, height], "nonempty_content": nonempty,
                     "a4_shape": a4_shape, "sufficient_resolution": width >= 700 and height >= 990})
    a4_pages = all(abs(page["size_pt"][0] - 595.28) < 2 and abs(page["size_pt"][1] - 841.89) < 2 for page in pages)
    ok = (pdf_magic and bool(pages) and len(pages) == len(pngs) and bool(pngs)
          and all(page["nonempty_content"] and all(value > 0 for value in page["size_pt"]) for page in pages)
          and all(item["magic"] and item["bytes"] > 1000 and item["nonempty_content"] and item["sufficient_resolution"] for item in pngs))
    if require_a4:
        ok = ok and a4_pages and all(item["a4_shape"] for item in pngs)
    return {"pdf": pdf_path, "png": [str(path) for path in png_paths], "ok": ok,
            "pdf_magic": pdf_magic, "pdf_bytes": len(data), "pdf_page_count": len(pages),
            "pdf_verification_engine": pdf_engine if fitz_ok else "structure_scan",
            "pdf_pages": pages, "a4_required": require_a4, "a4_page_sizes_ok": a4_pages,
            "png_checks": pngs, "png_page_count": len(pngs), "page_counts_match": len(pages) == len(pngs)}


def _save_spec(spec, workdir, stem):
    directory = Path(workdir) / "document_specs"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stem}.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return str(path)


def _document_bundle(spec, stem, outdir, workdir, exception_notes, deadline):
    outdir = Path(outdir)
    docx_path = outdir / f"{stem}.docx"
    writer = _write_docx(spec, docx_path, exception_notes)
    spec_path = _save_spec(spec, workdir, stem)
    docx_check = _validate_docx(docx_path, spec)
    if not docx_check["ok"]:
        raise RuntimeError(f"DOCX XML/package validation failed for {stem}: {docx_check['problems']}")
    pdf_path = outdir / f"{stem}_preview.pdf"
    prefix = f"{stem}_preview"
    engine = None
    remaining = deadline - time.monotonic()
    if render.which("soffice", "libreoffice") and remaining > 5:
        try:
            with tempfile.TemporaryDirectory(prefix="office_", dir=str(workdir)) as temporary:
                office = render.office_to_pdf(str(docx_path), temporary, timeout=min(18, remaining - 2))
                if office.get("ok"):
                    shutil.copyfile(office["pdf"], pdf_path)
                    raster = render.pdf_to_png(str(pdf_path), str(outdir), dpi=110, prefix=prefix,
                                               timeout=min(18, max(1, deadline - time.monotonic() - 2)))
                    if raster.get("ok"):
                        engine = {"engine": f"soffice+{raster['engine']}", "office_to_pdf": "soffice",
                                  "pdf_to_png": raster["engine"], "source": "docx"}
                        png_paths = raster["pages"]
                    else:
                        exception_notes.append({"code": "PREVIEW_RASTER_FALLBACK", "document": stem,
                                                "reason": raster.get("error", "PDF rasterisation failed")})
                else:
                    exception_notes.append({"code": "PREVIEW_OFFICE_FALLBACK", "document": stem,
                                            "reason": office.get("error", "Office conversion failed")})
        except Exception as exc:
            exception_notes.append({"code": "PREVIEW_OFFICE_FALLBACK", "document": stem, "reason": str(exc)[:300]})
        if engine is None:
            # Do not mix an office PDF with an independently generated PNG.
            result = preview.document_pdf_png(spec, str(outdir), prefix=prefix, pdf_path=str(pdf_path), dpi=110)
            engine = {"engine": "document_spec_matplotlib_fallback", "source": "document_spec"}
            png_paths = result["pages"]
    else:
        result = preview.document_pdf_png(spec, str(outdir), prefix=prefix, pdf_path=str(pdf_path), dpi=110)
        engine = {"engine": "document_spec_matplotlib_fallback", "source": "document_spec"}
        png_paths = result["pages"]
    preview_check = _preview_check(pdf_path, png_paths, True)
    if not preview_check["ok"]:
        raise RuntimeError(f"Preview validation failed for {stem}: {preview_check}")
    return {"document": stem, "docx_writer": writer, "docx": str(docx_path),
            "document_spec": spec_path, "docx_check": docx_check,
            "preview_engine": engine, "preview_check": preview_check,
            "files": [str(docx_path), str(pdf_path)] + [str(path) for path in png_paths]}


def build_deliverables(result, outdir, workdir) -> dict:
    """Generate all requested artifacts without sourcing external scientific data."""
    started = time.monotonic()
    document = _load_result(result)
    payload = _payload(document)
    outdir, workdir = Path(outdir).resolve(), Path(workdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    workdir.mkdir(parents=True, exist_ok=True)
    mpl_dir = workdir / "matplotlib"
    mpl_dir.mkdir(exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(mpl_dir))

    font_report = preview.resolve_font_preferences()
    font_resources = font_report["fonts"]
    font_warnings = copy.deepcopy(font_report["warnings"])
    fonts = {role: values["name"] for role, values in font_resources.items()}
    exception_notes = list(font_warnings)
    if not render.which("soffice", "libreoffice"):
        exception_notes.append({"code": "PREVIEW_ENGINE_FALLBACK", "severity": "warning",
                                "engine": "document_spec_matplotlib_fallback",
                                "reason": "PATH 中无 soffice/libreoffice；四份文档将由同一 document spec 实际排成 A4 PDF/PNG。"})
    report = {"ok": False, "engines": {"document_preview": {}, "font_resources": font_resources},
              "documents": {}, "preview_checks": {"documents": {}}, "exception_notes": exception_notes,
              "font_warnings": font_warnings, "files": [], "editable_specs": []}

    specs = (_metabolism_spec(payload), _enzyme_spec(payload), _immune_spec(payload), _advice_spec(payload))
    for stem, spec in zip(DOCUMENT_STEMS, specs):
        spec["fonts"] = fonts
        spec["font_resources"] = font_resources
        try:
            bundle = _document_bundle(spec, stem, outdir, workdir, exception_notes, started + 108)
            report["documents"][stem] = {"docx": bundle["docx"], "document_spec": bundle["document_spec"],
                                         "docx_check": bundle["docx_check"]}
            report["engines"].setdefault("docx_writers", {})[stem] = bundle["docx_writer"]
            report["engines"]["document_preview"][stem] = bundle["preview_engine"]
            report["preview_checks"]["documents"][stem] = bundle["preview_check"]
            report["files"].extend(bundle["files"])
            report["editable_specs"].append(bundle["document_spec"])
        except Exception as exc:
            exception_notes.append({"code": "DOCUMENT_BUILD_ERROR", "severity": "error",
                                    "document": stem, "reason": str(exc)[:500]})

    try:
        matrix = _matrix_spec(payload, exception_notes)
        matrix["font_resources"] = font_resources
        spec_path = Path(workdir) / "teaching_matrix_spec.json"
        spec_path.write_text(json.dumps(matrix, ensure_ascii=False, indent=2), encoding="utf-8")
        font_css = ", ".join(f"'{fonts[role]}'" for role in ("latin", "cjk", "mono")) + ", serif"
        svg_path = outdir / "teaching_matrix.svg"
        svg_path.write_text(_matrix_svg(matrix, font_css), encoding="utf-8")
        ET.parse(svg_path)
        pdf_path = outdir / "teaching_matrix_preview.pdf"
        png_path = outdir / "teaching_matrix_preview-1.png"
        graph_result = preview.diagram_png(matrix, str(png_path), str(pdf_path))
        graph_result["engine"] = "diagram_spec_matplotlib_fallback"
        graph_check = _preview_check(pdf_path, [png_path], False)
        graph_svg = svg_path.read_text(encoding="utf-8")
        graph_check["svg_editable"] = 'data-role="node"' in graph_svg and svg_path.stat().st_size > 500
        graph_check["explicit_edge_count"] = len(matrix["edges"])
        graph_check["ok"] = graph_check["ok"] and graph_check["svg_editable"]
        if not graph_check["ok"]:
            raise RuntimeError(f"Teaching matrix preview validation failed: {graph_check}")
        report["engines"]["teaching_matrix"] = {"engine": "diagram_spec_matplotlib_fallback", "source": "teaching_matrix_spec"}
        report["preview_checks"]["teaching_matrix"] = graph_check
        report["teaching_matrix"] = {"svg": str(svg_path), "spec": str(spec_path), "pdf": str(pdf_path),
                                     "png": str(png_path), "nodes": len(matrix["nodes"]), "edges": len(matrix["edges"])}
        report["files"].extend([str(svg_path), str(pdf_path), str(png_path)])
        report["editable_specs"].append(str(spec_path))
    except Exception as exc:
        exception_notes.append({"code": "TEACHING_MATRIX_BUILD_ERROR", "severity": "error", "reason": str(exc)[:500]})

    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    report["ok"] = (len(report["documents"]) == len(DOCUMENT_STEMS) and "teaching_matrix" in report
                    and not any(note.get("severity") == "error" for note in exception_notes))
    report["status"] = "complete" if report["ok"] else "partial"
    report_path = outdir / "deliverables_report.json"
    report["report"] = str(report_path)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Build editable teaching deliverables and real previews")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--result", required=True)
    build.add_argument("--outdir", required=True)
    build.add_argument("--workdir", required=True)
    args = parser.parse_args()
    if args.command == "build":
        try:
            report = build_deliverables(args.result, args.outdir, args.workdir)
        except Exception as exc:
            print(json.dumps({"ok": False, "engines": {}, "preview_checks": {},
                              "exception_notes": [{"code": "INPUT_ERROR", "severity": "error", "reason": str(exc)}]},
                             ensure_ascii=False))
            return 2
        print(json.dumps(report, ensure_ascii=False))
        return 0 if report.get("ok") else 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
