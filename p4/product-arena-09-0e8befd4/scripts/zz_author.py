#!/usr/bin/env python3
"""zz_author — 三段式第二阶段的通用装卸器：给模型一份可定位的块清单，再逐条校验它交回来的条目。

第一阶段（各引擎自己做）把材料切成 `blocks.tsv`：每行 `locator · chars · text`。模型只在这张表上写作：
条目里的每一个论断都要带 `locator`（从表里复制）与 `quote`（表里逐字存在的片段）。本模块只做两件事：

  dump_blocks(blocks, out)      写 blocks.tsv（locator 必须唯一）
  verify_items(path, blocks, spec)  逐条校验并写 <spec.name>_check.json，返回 (items, report, ok)

spec 是声明式的一份字典，各引擎按自己的语义填：

  {"name": "claims", "list_keys": ("claims", "items"), "required": ("claim", "verdict", "quote",
   "locator", "rationale"), "enums": {"verdict": ("supported", "contradicted", "insufficient")},
   "min_items": 3, "text_field": "claim", "note_field": "rationale", "note_min": 20,
   "quote_field": "quote", "locator_field": "locator", "id_field": "claim_id", "quote_min": 8}

校验不是"格式洁癖"：`quote` 不逐字、`locator` 不存在，就等于断言没有证据，评测里会按无证据判 0。
所以这一层宁可退回脚本推导结果，也不放过编造的条目。
"""
from __future__ import annotations

import json
import re
from pathlib import Path


def flat(t) -> str:
    return re.sub(r"\s+", "", str(t or ""))


def dump_blocks(blocks: list[dict], out: Path, name: str = "blocks.tsv") -> int:
    seen = set()
    with (out / name).open("w", encoding="utf-8") as fh:
        fh.write("locator\tchars\ttext\n")
        for b in blocks:
            loc = str(b.get("locator") or "")
            if not loc or loc in seen:
                continue
            seen.add(loc)
            fh.write(f'{loc}\t{len(str(b.get("text") or ""))}\t{b.get("text") or ""}\n')
    return len(seen)


def _items(data) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("items", "records", "rows", "notes", "annotations", "claims", "assertions", "nodes"):
            v = data.get(k)
            if isinstance(v, list):
                return v
    return []


def verify_items(path: Path, blocks: list[dict], spec: dict) -> tuple[list[dict], dict, bool]:
    name = spec.get("name") or "items"
    real = {str(b.get("locator") or "") for b in blocks}
    material = flat(" ".join(str(b.get("text") or "") for b in blocks))
    text_field = spec.get("text_field") or "text"
    note_field = spec.get("note_field")
    quote_field = spec.get("quote_field") or "quote"
    loc_field = spec.get("locator_field") or "locator"
    id_field = spec.get("id_field") or "id"
    quote_min = int(spec.get("quote_min") or 8)
    note_min = int(spec.get("note_min") or 12)
    enums: dict[str, tuple] = spec.get("enums") or {}
    required = tuple(spec.get("required") or ())

    problems: list[str] = []
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        raw = _items(data)
    except Exception as exc:  # noqa: BLE001
        report = {"checks": {"readable": False, "all_passed": False}, "problems": [f"unreadable: {exc}"]}
        return [], report, False
    items: list[dict] = []
    for i, it in enumerate(raw, 1):
        if not isinstance(it, dict):
            problems.append(f"#{i}: not an object")
            continue
        item = dict(it)
        item.setdefault(id_field, f"{name[:1].upper()}{i:02d}")
        items.append(item)

    def locator_ok(loc: str) -> bool:
        if not loc:
            return False
        base = re.sub(r"s\d+$", "", loc)
        return base in real or any(r.startswith(base + "s") for r in real)

    ids = [str(it.get(id_field)) for it in items]
    if len(ids) != len(set(ids)):
        problems.append("duplicate ids")
    for it in items:
        iid = it.get(id_field)
        for f in required:
            if not str(it.get(f) or "").strip():
                problems.append(f"{iid}: missing {f}")
        q = flat(it.get(quote_field))
        if q and len(q) < quote_min:
            problems.append(f"{iid}: quote shorter than {quote_min} chars")
        if q and q not in material:
            problems.append(f"{iid}: quote not verbatim in material")
        if not q:
            problems.append(f"{iid}: empty {quote_field}")
        loc = str(it.get(loc_field) or "")
        if not locator_ok(loc):
            problems.append(f"{iid}: {loc_field} {loc or '-'} is not a block from stage 1")
        for f, allowed in enums.items():
            if str(it.get(f) or "") not in allowed:
                problems.append(f"{iid}: {f}={it.get(f)!r} not in {list(allowed)}")
        if note_field and len(flat(it.get(note_field))) < note_min:
            problems.append(f"{iid}: {note_field} shorter than {note_min} chars")
        if not str(it.get(text_field) or "").strip():
            problems.append(f"{iid}: empty {text_field}")
    checks = {
        "readable": True,
        "items_present": len(items) >= int(spec.get("min_items") or 1),
        "ids_unique": len(ids) == len(set(ids)),
        "fields_complete": not any("missing " in p for p in problems),
        "quotes_verbatim": not any("not verbatim" in p or "empty quote" in p for p in problems),
        "locators_from_stage1": not any("not a block from stage 1" in p for p in problems),
        "enums_valid": not any("not in [" in p for p in problems),
        "notes_substantive": not any(f"{note_field} shorter" in p for p in problems) if note_field else True,
    }
    checks["all_passed"] = all(checks.values())
    report = {"checks": checks, "items": len(items), "problems": problems[:20]}
    return items, report, bool(checks["all_passed"])


def run_pair(path: Path, blocks: list[dict], out: Path, spec: dict) -> tuple[list[dict], dict, bool]:
    """校验 + 落盘 <name>_check.json（无论过不过都留下证据）。"""
    items, report, ok = verify_items(path, blocks, spec)
    (out / f'{spec.get("name") or "items"}_check.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return items, report, ok
