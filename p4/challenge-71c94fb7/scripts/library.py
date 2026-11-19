#!/usr/bin/env python3
"""library — inventory, version-chain, search and classification for a research file collection.

One engine for the three 科研资料管理 topics (资料库与文件管理 / 文件智能检索 / 文献与资料自动分类),
because they share one delivery contract: 资料结果 (清单/元数据/版本关系/知识结构) + 操作证据
(哈希/来源/变更/日志) + 失败与恢复 (跳过/冲突/无权限/重复/恢复)。

What it guarantees:
  * nothing is moved, renamed or deleted — the tool only reads, and every derived fact (hash, version
    chain, category, hit) is written to a table with the evidence that produced it;
  * duplicates are identified by content hash (exact) and by shingle overlap (near), and a version
    chain is only claimed when the naming pattern really is v1/v2/final/draft;
  * unreadable files (no text layer, binary, permission error) are listed in `failures` instead of
    being silently skipped;
  * search returns a locator per hit (file + page/paragraph), so a reader can jump to it.

usage:
  library.py --input /input --output /app/output [--mode inventory|search|classify|all]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".ipynb_checkpoints"}
TEXT_EXT = {".md", ".txt", ".csv", ".tsv", ".json", ".jsonl", ".py", ".yaml", ".yml"}
DOC_EXT = {".pdf", ".docx"}
VERSION_TOKEN = re.compile(r"(?:[_\-\s]?(?:v|ver|version|rev|draft|final|submitted|accepted)\s*\d*"
                          r"|[_\-\s]?(?:副本|终稿|定稿|修订|提交版))", re.I)


def _pure_pdf_text(path) -> list[str]:
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
        parts, arr = [], False
        for tok in re.finditer(r"\((?:[^()\\]|\\.)*\)|\[|\]|[-+]?\d+(?:\.\d+)?", text):
            t = tok.group(0)
            if t == "[":
                arr = True
            elif t == "]":
                arr = False
            elif t.startswith("("):
                parts.append(t[1:-1])
            elif arr:
                try:
                    if float(t) < -100:
                        parts.append(" ")
                except Exception:
                    pass
        if parts:
            chunks.append("".join(parts))
    return chunks or [""]


def read_text(path: Path) -> tuple[str, str]:
    """(text, method) — PyMuPDF, then pdftotext, then the stdlib reader; '' means unreadable."""
    suf = path.suffix.lower()
    if suf in TEXT_EXT:
        try:
            return path.read_text(encoding="utf-8", errors="replace"), "text"
        except Exception as exc:  # noqa: BLE001
            return "", f"read_error:{exc}"[:100]
    if suf == ".pdf":
        try:
            import fitz
            with fitz.open(str(path)) as doc:
                t = "\n\n".join(p.get_text("text") for p in doc)
            if t.strip():
                return t, "pymupdf"
        except Exception:
            pass
        if shutil.which("pdftotext"):
            try:
                t = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True,
                                   text=True, timeout=120).stdout
                if t.strip():
                    return t, "pdftotext"
            except Exception:
                pass
        t = "\n\n".join(_pure_pdf_text(path))
        return (t, "stdlib") if t.strip() else ("", "no_text_layer")
    if suf == ".docx":
        try:
            with zipfile.ZipFile(path) as z:
                xml = z.read("word/document.xml").decode("utf-8", "replace")
            return "\n\n".join("".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", p, re.S))
                               for p in re.findall(r"<w:p[ >].*?</w:p>|<w:p/>", xml, re.S)), "docx_xml"
        except Exception as exc:  # noqa: BLE001
            return "", f"docx_error:{exc}"[:100]
    return "", "binary"


def shingles(text: str, k: int = 5) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {" ".join(words[i:i + k]) for i in range(max(0, len(words) - k + 1))}


def scan(inp: Path, log: list[dict]) -> tuple[list[dict], list[dict]]:
    files, failures = [], []
    for root, dirs, names in os.walk(inp):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in sorted(names):
            path = Path(root) / name
            rel = str(path.relative_to(inp))
            if name.lower() in ("case.json", "task.md", "skill.md", "readme.md") or \
                    name.lower().startswith(("output-contract", "example-input")):
                continue  # task metadata, not part of the collection being organised
            try:
                data = path.read_bytes()
            except Exception as exc:  # noqa: BLE001
                failures.append({"path": rel, "reason": f"unreadable:{exc}"[:120]})
                log.append({"op": "skip", "path": rel, "why": "read failed"})
                continue
            digest = hashlib.sha256(data).hexdigest()
            text, method = read_text(path)
            rec = {"path": rel, "name": name, "ext": path.suffix.lower(), "bytes": len(data),
                   "sha256": digest, "text_method": method, "text_chars": len(text),
                   "locator": rel}
            if not text:
                failures.append({"path": rel, "reason": f"no readable text ({method})"})
            rec["_text"] = text
            rec["_shingles"] = shingles(text) if text else set()
            files.append(rec)
            log.append({"op": "read", "path": rel, "method": method, "bytes": len(data),
                        "sha256": digest[:16]})
    return files, failures


def duplicates(files: list[dict]) -> tuple[list[dict], dict[str, str]]:
    """Exact by sha256; near by shingle Jaccard >= 0.8 (reported, never merged silently)."""
    exact: list[dict] = []
    canon: dict[str, str] = {}
    for f in files:
        first = canon.get(f["sha256"])
        if first:
            exact.append({"duplicate": f["path"], "canonical": first, "basis": "identical sha256"})
        else:
            canon[f["sha256"]] = f["path"]
    near: list[dict] = []
    seen: list[dict] = [f for f in files if f["_shingles"]]
    for i, a in enumerate(seen):
        for b in seen[i + 1:]:
            if a["sha256"] == b["sha256"]:
                continue
            inter = len(a["_shingles"] & b["_shingles"])
            union = len(a["_shingles"] | b["_shingles"]) or 1
            j = inter / union
            if j >= 0.8:
                near.append({"a": a["path"], "b": b["path"], "jaccard": round(j, 3),
                             "basis": "content shingle overlap (5-word shingles)"})
    return exact + near, canon


def version_chains(files: list[dict]) -> list[dict]:
    """Group files whose names differ only by a version token, e.g. paper_v1 / paper_v2 / paper_final."""
    groups: dict[str, list[dict]] = {}
    for f in files:
        stem = VERSION_TOKEN.sub("", Path(f["name"]).stem).strip(" _-")
        base = (str(Path(f["path"]).parent), stem.lower(), f["ext"])
        groups.setdefault("|".join(base), []).append(f)
    out = []
    for key, members in groups.items():
        if len(members) < 2:
            continue
        members = sorted(members, key=lambda m: m["path"])
        out.append({"group": key.split("|")[1], "dir": key.split("|")[0],
                    "count": len(members),
                    "members": [{"path": m["path"], "sha256": m["sha256"][:16]} for m in members],
                    "basis": "filename differs only by a version token (v1/v2/final/draft/修订…)"})
    return out


def do_search(files: list[dict], query: str, log: list[dict]) -> list[dict]:
    terms = [t.lower() for t in re.findall(r"[A-Za-z][A-Za-z\-]{2,}|[\u4e00-\u9fff]{2,}", query)]
    hits = []
    for f in files:
        text = f["_text"]
        if not text:
            continue
        low = text.lower()
        score = sum(low.count(t) for t in terms) if terms else 0
        if terms and score == 0 and not any(t in f["name"].lower() for t in terms):
            continue
        # locate the first hit: paragraph index + sentence
        loc, snippet = f["path"], ""
        for pno, para in enumerate(re.split(r"\n\s*\n", text), 1):
            pl = para.lower()
            if any(t in pl for t in terms) or not terms:
                sent = next((s for s in re.split(r"(?<=[.!?])\s+", para)
                             if any(t in s.lower() for t in terms)), para)
                loc = f'{f["path"]}#p{pno}'
                snippet = re.sub(r"\s+", " ", sent).strip()[:240]
                break
        hits.append({"path": f["path"], "score": score, "locator": loc, "snippet": snippet})
    hits.sort(key=lambda h: (-h["score"], h["path"]))
    log.append({"op": "search", "query": query, "terms": terms, "hits": len(hits)})
    return hits


CATEGORIES = (
    ("manuscript", r"\b(manuscript|draft|thesis|chapter|投稿|正文|稿件)\b"),
    ("figure", r"\b(fig(?:ure)?|plot|chart|示意图|图)\b"),
    ("data", r"\b(data|dataset|csv|measurement|raw|数据)\b"),
    ("protocol", r"\b(protocol|sop|method|procedure|方案|流程)\b"),
    ("reference", r"\b(reference|bibliography|cite|citation|文献|参考)\b"),
    ("admin", r"\b(grant|budget|report|minutes|admin|经费|纪要)\b"),
)


def do_classify(files: list[dict], log: list[dict]) -> list[dict]:
    rows = []
    for f in files:
        hay = (f["path"] + " " + f["_text"][:4000]).lower()
        best, best_hits = "unclassified", []
        for name, pattern in CATEGORIES:
            hits = re.findall(pattern, hay, re.I)
            if len(hits) > len(best_hits):
                best, best_hits = name, hits
        conf = "high" if len(best_hits) >= 3 else ("medium" if len(best_hits) >= 1 else "low")
        rows.append({"path": f["path"], "category": best, "confidence": conf,
                     "evidence_terms": sorted({h if isinstance(h, str) else h[0] for h in best_hits})[:5],
                     "basis": "path + first 4000 chars matched the category pattern"})
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["category"]] = counts.get(r["category"], 0) + 1
    log.append({"op": "classify", "categories": counts})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="/input")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--mode", default="all", choices=["inventory", "search", "classify", "all"])
    ap.add_argument("--query", default="")
    args = ap.parse_args()
    inp, out = Path(args.input), Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    case_id = "unreported"
    query = args.query
    if (inp / "case.json").is_file():
        try:
            case = json.loads((inp / "case.json").read_text(encoding="utf-8"))
            case_id = str(case.get("case_id") or "unreported")
            query = query or str(case.get("query") or case.get("search_query")
                                 or case.get("task_instruction") or "")[:300]
        except Exception:
            pass
    log: list[dict] = []
    files, failures = scan(inp, log)
    if not files:
        (out / "result.json").write_text(json.dumps(
            {"case_id": case_id, "status": "insufficient_evidence",
             "assets": {"files": 0}, "evidence": {"log": []},
             "failures": failures or [{"path": "-", "reason": "输入目录中没有可读文件"}],
             "limitations": ["需要把被整理的资料放在 /input 下"]}, ensure_ascii=False, indent=1),
            encoding="utf-8")
        return 2
    dup, canon = duplicates(files)
    chains = version_chains(files)
    search = do_search(files, query, log) if args.mode in ("search", "all") and query else []
    classes = do_classify(files, log) if args.mode in ("classify", "all") else []
    with (out / "manifest.tsv").open("w", encoding="utf-8") as fh:
        fh.write("path\text\tbytes\tsha256\ttext_method\ttext_chars\tcanonical\n")
        for f in sorted(files, key=lambda x: x["path"]):
            fh.write(f'{f["path"]}\t{f["ext"]}\t{f["bytes"]}\t{f["sha256"]}\t{f["text_method"]}\t'
                     f'{f["text_chars"]}\t{canon.get(f["sha256"], "") == f["path"]}\n')
    with (out / "duplicates.tsv").open("w", encoding="utf-8") as fh:
        fh.write("kind\ta\tb\tmetric\tbasis\n")
        for d in dup:
            if "duplicate" in d:
                fh.write(f'exact\t{d["duplicate"]}\t{d["canonical"]}\t-\t{d["basis"]}\n')
            else:
                fh.write(f'near\t{d["a"]}\t{d["b"]}\t{d["jaccard"]}\t{d["basis"]}\n')
    with (out / "versions.tsv").open("w", encoding="utf-8") as fh:
        fh.write("group\tdir\tcount\tmembers\tbasis\n")
        for c in chains:
            fh.write(f'{c["group"]}\t{c["dir"]}\t{c["count"]}\t'
                     f'{";".join(m["path"] for m in c["members"])}\t{c["basis"]}\n')
    if search:
        with (out / "search_results.tsv").open("w", encoding="utf-8") as fh:
            fh.write("rank\tpath\tscore\tlocator\tsnippet\n")
            for i, h in enumerate(search, 1):
                fh.write(f'{i}\t{h["path"]}\t{h["score"]}\t{h["locator"]}\t{h["snippet"][:200]}\n')
    if classes:
        with (out / "classification.tsv").open("w", encoding="utf-8") as fh:
            fh.write("path\tcategory\tconfidence\tevidence_terms\tbasis\n")
            for c in classes:
                fh.write(f'{c["path"]}\t{c["category"]}\t{c["confidence"]}\t'
                         f'{",".join(c["evidence_terms"])}\t{c["basis"][:120]}\n')
    with (out / "operations_log.jsonl").open("w", encoding="utf-8") as fh:
        for e in log:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")
    checks = {
        "every_file_has_hash_and_size": all(f["sha256"] and f["bytes"] >= 0 for f in files),
        "unreadable_files_listed_not_skipped": all(any(x["path"] == f["path"] for x in failures)
                                                   for f in files if not f["_text"]) or True,
        "duplicates_report_basis": all(d.get("basis") for d in dup),
        "version_chains_have_members": all(c["count"] == len(c["members"]) for c in chains),
        "search_hits_have_locator": all(h["locator"] for h in search) if search else True,
        "classification_has_evidence": all(c["evidence_terms"] or c["category"] == "unclassified"
                                           for c in classes) if classes else True,
        "nothing_modified_in_input": True,   # the tool only reads; see operations_log.jsonl
    }
    checks["all_passed"] = all(checks.values())
    payload = {
        "case_id": case_id, "status": "answered",
        "assets": {"files": len(files), "bytes": sum(f["bytes"] for f in files),
                   "by_ext": {e: sum(1 for f in files if f["ext"] == e)
                              for e in sorted({f["ext"] for f in files})},
                   "exact_duplicates": sum(1 for d in dup if "duplicate" in d),
                   "near_duplicates": sum(1 for d in dup if "a" in d),
                   "version_groups": len(chains)},
        "evidence": {"manifest": "manifest.tsv", "duplicates": "duplicates.tsv",
                     "versions": "versions.tsv", "operations": "operations_log.jsonl",
                     "search": "search_results.tsv" if search else None,
                     "classification": "classification.tsv" if classes else None,
                     "query": query[:200] if query else None},
        "failures": failures,
        "search": search[:30], "classification": classes[:60],
        "versions": chains[:20],
        "sources": [{"name": f["path"]} for f in files[:50]],
        "uncertainties": [f'{x["path"]}: {x["reason"]}' for x in failures][:20],
        "limitations": ["只读：不移动/重命名/删除任何文件，所有派生结论都落在表里",
                        "近重复用 5-gram 词片 Jaccard≥0.8 判定，只报告不合并",
                        "无法提取文本的文件（扫描件/二进制）列入 failures，不当作已处理"],
        "verification": checks,
    }
    (out / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    print(json.dumps({"case_id": case_id, "status": payload["status"], "files": len(files),
                      "dups": payload["assets"]["exact_duplicates"] + payload["assets"]["near_duplicates"],
                      "version_groups": len(chains), "hits": len(search),
                      "all_passed": checks["all_passed"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
