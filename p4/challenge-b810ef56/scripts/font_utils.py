#!/usr/bin/env python3
"""Local font selection and structured glyph warnings; never downloads fonts."""
from __future__ import annotations

import re
import unicodedata


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _cjk_needed(text: str) -> bool:
    return any("\u3400" <= ch <= "\u9fff" for ch in text)


def policy(required: str | None = None, text: str = "") -> dict:
    """Return an honest font policy usable by matplotlib and preview reports."""
    required = (required or "Liberation Serif").strip()
    report = {"requested_font": required, "requested_available": False,
              "selected_fonts": [], "warnings": [], "glyph_audit": "not_reported"}
    try:
        from matplotlib import font_manager
        installed = {f.name: f for f in font_manager.fontManager.ttflist}
        lookup = {_key(name): name for name in installed}
        actual = lookup.get(_key(required))
        report["requested_available"] = actual is not None
        families = [actual] if actual else []
        if not actual:
            report["warnings"].append({
                "code": "font_unavailable", "severity": "warning", "font": required,
                "compliance": False,
                "reason": "The requested font family is not installed in the local renderer; the editable source still records the requested family, while previews use the disclosed fallback.",
                "fallback": "local serif plus a local CJK family when needed"
            })
        if _cjk_needed(text):
            cjk_names = ("Noto Sans CJK SC", "Noto Sans CJK JP", "Source Han Sans SC",
                         "Source Han Sans CN", "WenQuanYi Zen Hei", "WenQuanYi Micro Hei",
                         "Microsoft YaHei", "SimHei", "PingFang SC", "Heiti SC",
                         "Arial Unicode MS", "Droid Sans Fallback")
            cjk = next((lookup[_key(n)] for n in cjk_names if _key(n) in lookup), None)
            if cjk:
                families.append(cjk)
            else:
                report["warnings"].append({"code": "cjk_font_unavailable", "severity": "warning",
                                            "reason": "No preferred local CJK font was found; glyph coverage is audited below."})
        families.append("DejaVu Serif")
        families.append("DejaVu Sans")
        report["selected_fonts"] = list(dict.fromkeys(families))
        report["glyph_audit"] = audit_glyphs(text, report["selected_fonts"], font_manager)
        for item in report["glyph_audit"].get("warnings") or []:
            report["warnings"].append(item)
    except Exception as exc:  # noqa: BLE001
        report["selected_fonts"] = ["serif", "sans-serif"]
        report["warnings"].append({"code": "font_probe_unavailable", "severity": "warning",
                                    "reason": str(exc)[:240], "fallback": report["selected_fonts"]})
        report["glyph_audit"] = {"status": "not_reported",
                                  "reason": "The local font registry could not be inspected."}
    return report


def audit_glyphs(text: str, families: list[str], font_manager=None) -> dict:
    try:
        from fontTools.ttLib import TTFont
        if font_manager is None:
            from matplotlib import font_manager as fm
            font_manager = fm
    except Exception as exc:  # noqa: BLE001
        return {"status": "not_reported", "reason": "fontTools is unavailable: " + str(exc)[:160],
                "warnings": [{"code": "glyph_audit_unavailable", "severity": "warning",
                              "reason": "Rendering continues, but missing glyphs could not be enumerated."}]}
    cmaps = []
    used = []
    for family in families:
        try:
            path = font_manager.findfont(family, fallback_to_default=False)
            font = TTFont(path, fontNumber=0, lazy=True)
            cmaps.append(set(font.getBestCmap() or {}))
            used.append({"family": family, "path": path})
            font.close()
        except Exception:
            continue
    if not cmaps:
        return {"status": "not_reported", "reason": "No selected font file could be opened.",
                "warnings": [{"code": "glyph_audit_unavailable", "severity": "warning",
                              "reason": "Rendering continues without a character-coverage audit."}]}
    chars = sorted({ch for ch in text if not ch.isspace() and not unicodedata.category(ch).startswith("C")})
    missing = [ch for ch in chars if not any(ord(ch) in cmap for cmap in cmaps)]
    warnings = []
    if missing:
        warnings.append({"code": "missing_glyphs", "severity": "warning", "compliance": False,
                         "characters": missing[:60], "count": len(missing),
                         "codepoints": [f"U+{ord(ch):04X}" for ch in missing[:60]],
                         "scope": "document preview", "fallback_fonts": families,
                         "reason": "These characters have no mapped glyph in any selected local font; rendering continues and a human must supply an appropriate font."})
    return {"status": "complete", "audited_characters": len(chars), "missing_count": len(missing),
            "fonts": used, "warnings": warnings}


def configure_matplotlib(required: str | None = None, text: str = "") -> dict:
    import matplotlib
    report = policy(required, text)
    families = report["selected_fonts"]
    matplotlib.rcParams["font.family"] = "serif"
    matplotlib.rcParams["font.serif"] = families
    matplotlib.rcParams["font.sans-serif"] = list(reversed(families))
    matplotlib.rcParams["axes.unicode_minus"] = False
    return report
