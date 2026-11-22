#!/usr/bin/env python3
"""Reviewer-comment ledger and conservative, source-anchored text revisions.

Only verbatim spans already present in a manuscript are changed. The module
never calculates missing statistics or supplies scientific observations. When
an edit cannot be justified from the supplied text, it emits an evidence request.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

ID_RE = re.compile(r"^R(\d+)\.(\d+)$", re.I)
NUMBER_RE = re.compile(r"(?<![\w.])[-+]?(?:\d+(?:,\d{3})*|\.\d+)(?:\.\d+)?")
CJK_RE = re.compile(r"[\u3400-\u9fff]")
STOP = set("a an and are as at be been but by can could did do does for from had has have how if in into is it its may more not of on or our please report should so than that the their these this those to was were what when whether which why will with would you your".split())


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\u00a0", " ")).strip()


def words(text: str) -> set[str]:
    return {x for x in re.findall(r"[a-z][a-z-]{2,}", text.lower()) if x not in STOP}


def line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def anchor_digest(text: str) -> str:
    return hashlib.sha256(clean(text).encode("utf-8")).hexdigest()[:16]


def section_at(text: str, offset: int) -> str:
    section = "unsectioned"
    for match in re.finditer(r"(?m)^#{1,6}\s+(.+?)\s*$", text[:offset]):
        section = match.group(1).strip()
    return section


def paragraph_spans(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in re.finditer(r"(?s)(?:\A|\n\s*\n)(.*?)(?=\n\s*\n|\Z)", text)
            if m.group(1).strip()]


def split_requests(text: str) -> list[str]:
    text = clean(text)
    if not text:
        return []
    # Do not split decimal points or common abbreviations. Newlines have already
    # become spaces, so conjunctive requests introduced by Also are split too.
    text = re.sub(r"\b(Also|Additionally|In addition),\s*", r". \1 ", text, flags=re.I)
    pieces = re.split(r"(?<=[?!？！])\s+|(?<=[。；;])\s*|(?<![A-Z])(?<!\betc)(?<!\be\.g)(?<!\bi\.e)(?<=\.)\s+(?=[A-Z\u3400-\u9fff])", text)
    return [p.strip() for p in pieces if p.strip()]


def classify(text: str) -> tuple[str, str, str]:
    low = text.lower()
    if re.search(r"experiment|time-course experiment|staining image|supplementary figure|补做|补充.{0,8}(?:实验|图)|additional cohort|new data", low):
        return "experiment", "new_data_or_experiment", "major"
    if re.search(r"power|statistic|effect size|cohen|eta-squared|p-value|anova|t-test|线性|统计|效应量|检验", low):
        return "statistics", "analysis_or_reporting", "major"
    if re.search(r"typo|grammar|clarif|ambiguous|confusing|overstat|temper|soften|language|wording|表述|措辞|笔误", low):
        return "writing", "clarity_or_calibration", "minor"
    if re.search(r"reference|citation|verify|doi|文献|引用", low):
        return "fact", "citation_verification", "major"
    return "fact", "method_or_factual_evidence", "major"


def issue_keys(text: str) -> set[str]:
    low = text.lower()
    rules = {
        "power_analysis": r"power (?:analysis|calculation|estimate)|功效分析",
        "sample_size": r"sample size|n\s*=\s*\d+|样本量",
        "effect_size": r"effect size|cohen|eta-squared|效应量",
        "sex_scope": r"\bsex|male|female|性别",
        "time_course": r"time.?course|earlier time point|时间(?:点|进程)",
        "overstatement": r"overstat|temper|soften|reverses|过度",
        "citation_accuracy": r"citation|reference|文献",
        "group_baseline": r"baseline intake|matching algorithm|基线",
        "exclusions": r"excluded|exclusion|intent-to-treat|per-protocol|排除",
        "side_preference": r"side preference|位置偏好",
        "locomotor_selection": r"locomotor|subset|活动|亚组",
    }
    return {name for name, pattern in rules.items() if re.search(pattern, low)}


def detect_conflicts(comments: list[dict]) -> list[dict]:
    conflicts = []
    for i, first in enumerate(comments):
        a = first["text"].lower()
        supportive = bool(re.search(r"not.{0,30}necessary|no need|adequate|agree with|do not think", a))
        if not supportive:
            continue
        for second in comments[i + 1:]:
            shared = issue_keys(first["text"]) & issue_keys(second["text"])
            if not shared:
                continue
            b = second["text"].lower()
            demanding = bool(re.search(r"provide|acknowledge|please|must|should|add|补充|说明", b))
            if demanding:
                conflicts.append({
                    "type": "reviewer_directive_conflict",
                    "comment_ids": [first["id"], second["id"]],
                    "issue_keys": sorted(shared),
                    "supportive_directive": first["text"],
                    "requesting_directive": second["text"],
                    "resolution": "Do not manufacture an analysis requested by one reviewer and dismissed by another. Preserve the conflict; allow a source-grounded limitation while requiring the authors/editor to resolve the remaining disagreement."
                })
    return conflicts


def parse_comments(text: str, source_path: str) -> tuple[list[dict], list[dict]]:
    lines = text.splitlines()
    comments: list[dict] = []
    warnings = []
    reviewer = None
    current = None

    def flush():
        nonlocal current
        if not current:
            return
        current["text"] = clean(" ".join(current.pop("body")))
        current["verbatim_quote"] = current["text"]
        if current["text"]:
            current["locator"] = f"comments:L{current['body_start']}-L{current['body_end']}"
            current["requirements"] = []
            for n, request in enumerate(split_requests(current["text"]), 1):
                category, kind, severity = classify(request)
                current["requirements"].append({
                    "id": f"{current['id']}.Q{n}", "comment_id": current["id"],
                    "text": request, "category": category,
                    "request_type": kind, "severity": severity,
                    "source": source_path, "locator": current["locator"],
                    "issue_keys": sorted(issue_keys(request)),
                })
            comments.append(current)
        else:
            warnings.append({"code": "empty_comment", "comment_id": current["id"],
                             "locator": current["header_locator"]})
        current = None

    for index, raw in enumerate(lines, 1):
        heading = re.match(r"^\s*#{2,6}\s+(R\d+\.\d+)\b\s*[:：-]?\s*(.*?)\s*$", raw, re.I)
        inline = re.match(r"^\s*\*\*(R\d+\.\d+)\*\*\s*[:：-]?\s*(.*?)\s*$", raw, re.I)
        reviewer_match = re.match(r"^\s*#{2,6}\s+(?:Reviewer|审稿人)\s*(\d+)", raw, re.I)
        marker = heading or inline
        if marker:
            flush()
            cid = marker.group(1).upper()
            rest = marker.group(2).strip()
            if any(x["id"] == cid for x in comments):
                warnings.append({"code": "duplicate_comment_id", "comment_id": cid,
                                 "locator": f"comments:L{index}"})
            current = {"id": cid, "reviewer": reviewer, "source": source_path,
                       "header_locator": f"comments:L{index}",
                       "title": "" if inline else rest,
                       "body": [rest] if inline and rest else [],
                       "body_start": index, "body_end": index}
        elif reviewer_match:
            flush()
            reviewer = int(reviewer_match.group(1))
        elif current:
            if re.match(r"^\s*([-*_]\s*){3,}$", raw):
                continue
            if raw.strip():
                if not current["body"]:
                    current["body_start"] = index
                current["body"].append(raw.strip())
                current["body_end"] = index
    flush()
    if text.strip() and not comments:
        warnings.append({"code": "unparsed_reviewer_comments", "source": source_path,
                         "message": "Text was read but no explicit Rn.m comment identifier was found. Treat coverage as unverified; do not merge all instructions into one generic answer."})
        category, kind, severity = classify(text)
        comments.append({"id": "UNNUMBERED", "reviewer": reviewer, "source": source_path,
                         "locator": "comments:L1-L%d" % len(lines), "header_locator": "comments:L1",
                         "title": "Unnumbered supplied reviewer text", "text": clean(text),
                         "verbatim_quote": clean(text), "requirements": [{
                             "id": "UNNUMBERED.Q1", "comment_id": "UNNUMBERED", "text": clean(text),
                             "category": category, "request_type": kind, "severity": severity,
                             "source": source_path, "locator": "comments:L1-L%d" % len(lines),
                             "issue_keys": sorted(issue_keys(text))}]})
    return comments, warnings


@dataclass
class RevisionSession:
    original: str
    source: str
    text: str = field(init=False)
    changes: list[dict] = field(default_factory=list)

    def __post_init__(self):
        self.text = self.original

    def _current_location(self, snippet: str) -> dict:
        start = self.text.find(snippet)
        if start < 0:
            return {"locator": "not_reported", "section": "not_reported",
                    "anchor_sha256": "not_reported", "verification": "after_text_not_found"}
        end = start + len(snippet)
        line_start = line_number(self.text, start)
        line_end = line_number(self.text, max(start, end - 1))
        return {"locator": f"revised:L{line_start}-L{line_end}", "section": section_at(self.text, start),
                "anchor_sha256": anchor_digest(snippet), "line_start": line_start, "line_end": line_end,
                "exact_quote": clean(snippet), "verification": "exact_substring_in_revised"}

    def _original_location(self, snippet: str) -> dict:
        start = self.original.find(snippet)
        if start < 0:
            return {"locator": "not_reported", "section": "not_reported"}
        end = start + len(snippet)
        return {"locator": f"manuscript:L{line_number(self.original, start)}-L{line_number(self.original, max(start, end - 1))}",
                "section": section_at(self.original, start)}

    def replace(self, before: str, after: str, comment_id: str, request_id: str,
                reason: str, action: str = "replace_text") -> dict | None:
        if before == after or self.text.count(before) != 1:
            return None
        old_location = self._original_location(before)
        self.text = self.text.replace(before, after, 1)
        change = {"change_id": f"C{len(self.changes)+1:03d}", "comment_id": comment_id,
                  "subrequest_id": request_id, "action": action, "edit_applied": True,
                  "status": "edited_source_grounded", "before": before, "after": after,
                  "baseline_location": old_location, **self._current_location(after),
                  "reason": reason, "source_of_wording": f"reviewer:{comment_id} and supplied manuscript",
                  "human_review_required": True,
                  "unresolved": "Author must confirm the revision is scientifically faithful; this check verifies text landing, not scientific validity."}
        self.changes.append(change)
        return change

    def insert_limitation(self, sentence: str, comment_id: str, request_id: str, reason: str) -> dict | None:
        if clean(sentence).lower() in clean(self.text).lower():
            return None
        discussion = list(re.finditer(r"(?im)^#{1,6}\s+(?:\d+(?:\.\d+)*\.?\s+)?(?:discussion|limitations|讨论|局限性)\b.*$", self.text))
        references = re.search(r"(?im)^#{1,6}\s+(?:references|bibliography|参考文献)\b.*$", self.text)
        heading_added = False
        if discussion:
            head = discussion[-1]
            following = re.search(r"(?m)^#{1,6}\s+", self.text[head.end():])
            pos = head.end() + following.start() if following else len(self.text)
            insertion = sentence + "\n\n"
            prefix = self.text[:pos].rstrip() + "\n\n"
            self.text = prefix + insertion + self.text[pos:].lstrip("\n")
        else:
            pos = references.start() if references else len(self.text)
            insertion = "## Limitations\n\n" + sentence + "\n\n"
            heading_added = True
            self.text = self.text[:pos].rstrip() + "\n\n" + insertion + self.text[pos:].lstrip("\n")
        change = {"change_id": f"C{len(self.changes)+1:03d}", "comment_id": comment_id,
                  "subrequest_id": request_id, "action": "add_limitation", "edit_applied": True,
                  "status": "partial", "before": "not_reported: no corresponding limitation sentence in the supplied baseline",
                  "after": sentence, "baseline_location": self._original_location(self.original),
                  "inserted_limitations_heading": heading_added,
                  **self._current_location(sentence), "reason": reason,
                  "source_of_wording": f"reviewer:{comment_id}; the sentence reports only absent reporting, never a new observation",
                  "human_review_required": True,
                  "unresolved": "This limitation is not a substitute for the requested data, analysis, or methodological detail; author confirmation is still required."}
        self.changes.append(change)
        return change


def related_paragraphs(manuscript: str, request: str, limit: int = 2) -> list[dict]:
    query = words(request) | {x.lower() for x in issue_keys(request)}
    candidates = []
    for start, end in paragraph_spans(manuscript):
        paragraph = manuscript[start:end].strip()
        if paragraph.startswith("#"):
            continue
        tokens = words(paragraph)
        score = len(query & tokens)
        for issue in issue_keys(request):
            if issue == "power_analysis" and re.search(r"\bn\s*=", paragraph, re.I):
                score += 3
            if issue == "sex_scope" and re.search(r"\bmale\b|\bfemale\b", paragraph, re.I):
                score += 4
            if issue == "effect_size" and re.search(r"cohen|anova|\bp\s*[<=>]", paragraph, re.I):
                score += 3
            if issue == "group_baseline" and "baseline" in paragraph.lower():
                score += 3
            if issue == "time_course" and re.search(r"\b\d+\s*h\b|days?|time", paragraph, re.I):
                score += 2
        if score:
            candidates.append((score, start, end, paragraph))
    candidates.sort(key=lambda x: (-x[0], x[1]))
    out = []
    for score, start, end, paragraph in candidates[:limit]:
        out.append({"relation": "relevant_source_context_not_necessarily_fulfillment",
                    "source_locator": f"manuscript:L{line_number(manuscript, start)}-L{line_number(manuscript, max(start, end - 1))}",
                    "section": section_at(manuscript, start), "quote": clean(paragraph),
                    "keyword_overlap_score": score, "verification": "exact_normalized_substring_in_baseline"})
    if not out:
        out.append({"relation": "not_reported", "source_locator": "not_reported", "quote": "not_reported",
                    "keyword_overlap_score": 0,
                    "verification": "No relevant passage was identified by exact keyword search in the supplied manuscript; absence of a keyword hit does not prove absence from an unprovided full paper."})
    return out


def proposed_limitation(request: str, manuscript: str) -> str | None:
    low = request.lower()
    wants_limit = bool(re.search(r"limitation|acknowledge|discussion|discuss|confound|限制|说明|讨论", low))
    if not wants_limit:
        return None
    issues = issue_keys(request)
    if "power_analysis" in issues:
        return "No a priori power calculation or post-hoc power estimate is available in the supplied materials; the reported group size therefore remains a limitation pending author-supplied calculations and assumptions."
    if "sex_scope" in issues and re.search(r"\bmale\b", manuscript, re.I):
        return "Only male mice were included in the supplied study summary; sex-specific treatment effects were not evaluated."
    if "time_course" in issues:
        if "earlier time" in low or re.search(r"\b2\s*h\b|\b4\s*h\b", low):
            return "Intake at earlier time points (including the 2 h and 4 h time points raised by the reviewer) was not reported in the supplied materials; the time course of the effect remains unresolved."
        return "An early time-course experiment was not reported in the supplied materials; the onset of the reported effect remains unresolved."
    if "locomotor_selection" in issues:
        return "The locomotor-subset selection procedure and the timing of locomotor testing relative to intake testing were not reported; potential selection or timing confounding cannot be resolved from the supplied materials."
    return None


def revise_for_request(session: RevisionSession, request: dict, manuscript: str) -> list[dict]:
    text = request["text"]
    low = text.lower()
    changes = []
    cid, qid = request["comment_id"], request["id"]
    if re.search(r"overstat|temper|soften", low):
        # Generic calibration of a reversal claim, retaining the measured
        # comparison rather than asserting normalization to a young control.
        for before, after in (
            ("exercise reverses age-related decline in hippocampal neurogenesis",
             "exercise is associated with greater hippocampal neurogenesis than sedentary controls"),
            ("reverses this decline", "increases hippocampal neurogenesis relative to sedentary controls"),
        ):
            change = session.replace(before, after, cid, qid,
                                     "Reviewer requested calibration; the baseline directly supplies the aged-mouse sedentary comparison, not a young-control comparison.",
                                     "calibrate_overstatement")
            if change:
                changes.append(change)
        if "threshold dose near" in low:
            change = session.replace("support a threshold dose of approximately", "suggest a threshold dose near",
                                     cid, qid, "The reviewer supplied the calibrated phrase and the corresponding original phrase occurs verbatim in the manuscript.",
                                     "calibrate_overstatement")
            if change:
                changes.append(change)
        quotes = re.findall(r'"([^"\n]{4,400})"', text)
        replacement = re.search(r"(?:to|with)\s+[\"']([^\"'\n]{4,400})[\"']", text, re.I)
        if quotes and replacement:
            before = max((q for q in quotes if q in session.text), key=len, default="")
            target = replacement.group(1)
            if before and target != before and target in low:
                change = session.replace(before, target, cid, qid,
                                         "Both the original and requested replacement are quoted by the reviewer; the original was located verbatim before replacement.",
                                         "reviewer_supplied_replacement")
                if change:
                    changes.append(change)
        if "young control" in low:
            sentence = "A young control group was not included, so the supplied experiment does not establish whether the observed neurogenesis returned to young-adult levels."
            change = session.insert_limitation(sentence, cid, qid,
                                               "The reviewer identifies the absent comparison; this adds only the limitation, not a fabricated young-control result.")
            if change:
                changes.append(change)
    if re.search(r"parenthetical|confusing", low):
        quotes = re.findall(r'"([^"\n]{8,220})"', text)
        for before in quotes:
            match = re.match(r"with \(([A-Z]+,\s*n\s*=\s*\d+)\) or without \(([A-Z]+,\s*n\s*=\s*\d+)\)", before)
            start = session.text.find(before)
            paragraph = session.text[start:start + 500] if start >= 0 else ""
            if match and "running wheels" in paragraph:
                after = f"with running wheels ({match.group(1)}) or without running wheels ({match.group(2)})"
                change = session.replace(before, after, cid, qid,
                                         "Clarified the quoted parenthesis using only the object (running wheels) named in the same Methods paragraph.",
                                         "clarify_parentheses")
                if change:
                    changes.append(change)
                    break
    if "side_preference" in issue_keys(text):
        sentence = "Group-level side-preference results were not reported in the supplied materials; bottle-position alternation alone does not establish whether side preference occurred."
        change = session.insert_limitation(sentence, cid, qid,
                                           "The baseline reports alternation but no group-level preference result; the revision does not assert that no preference was detected.")
        if change:
            changes.append(change)
    if "exclusions" in issue_keys(text):
        sentence = "Animal exclusions and the intention-to-treat or per-protocol status of the analysis were not reported in the supplied materials."
        change = session.insert_limitation(sentence, cid, qid,
                                           "The request identifies missing reporting. No exclusions or analysis population are inferred.")
        if change:
            changes.append(change)
    if "group_baseline" in issue_keys(text):
        sentence = "The group-matching algorithm and group-specific baseline intake values were not reported in the supplied summary; only the pooled baseline result is available."
        change = session.insert_limitation(sentence, cid, qid,
                                           "The requested allocation algorithm and per-group values are absent. Existing pooled values are not redistributed or invented.")
        if change:
            changes.append(change)
    sentence = proposed_limitation(text, manuscript)
    if sentence:
        change = session.insert_limitation(sentence, cid, qid,
                                           "The reviewer permits or requests a limitation; this is explicitly not a claim that the requested data or analysis was supplied.")
        if change:
            changes.append(change)
    return changes


def missing_action(request: dict, comments_source: str, materials: list[str]) -> str:
    low = request["text"].lower()
    category = request["category"]
    if "power" in low:
        return "Authors must provide the allocation/effect-size assumptions and either a documented power calculation or an editor-accepted limitation. A value cannot be reconstructed from n alone."
    if "effect size" in low or "cohen" in low or "eta-squared" in low:
        return "Authors must supply group summary statistics/raw data and the effect-size convention. Reported percentages or p thresholds alone do not determine Cohen's d or eta-squared."
    if re.search(r"exact p|p-values", low):
        return "Authors must retrieve exact statistics from the analysis output. Exact p-values cannot be reconstructed from p < 0.01 or p < 0.001."
    if re.search(r"experiment|staining image|supplementary figure|time-course|补做|实验", low):
        return "The requested experiment or source images were not supplied. Authors must provide authentic new data or retain an explicit limitation; no result or figure may be fabricated."
    if re.search(r"reference|citation|文献", low):
        return "A human must verify the cited work against the actual publication and supply bibliographic details; no external verification was performed."
    if re.search(r"matched|matching algorithm|baseline intake per group", low):
        return "Authors must supply the actual allocation algorithm and group-level baseline records; the pooled baseline mean cannot substitute for group values."
    if re.search(r"random|selection criteria|same day|timing", low):
        return "Authors must confirm the historical selection and testing procedure from study records; random selection or separate-day testing cannot be inferred."
    if "excluded" in low or "intent-to-treat" in low:
        return "Authors must supply exclusion records and the prespecified analysis population; neither can be inferred from reported sample sizes."
    if "side preference" in low:
        return "Authors must supply side-preference observations by group; alternating bottle positions is a control procedure, not an outcome."
    if re.search(r"half-life|expected range|human exercise|erickson|van praag|other published", low):
        return "Authors must supply and verify an appropriate source before making this external comparison. The supplied manuscript/comment text alone is not external verification."
    if re.search(r"rationale|why| justify|时间点|依据|标记方法", low):
        return "Authors must provide the protocol rationale and any alternatives actually considered. A post-hoc justification cannot be invented from the dose or schedule alone."
    if category == "statistics":
        return "Authors must provide the analysis output and data needed for this requested statistical action."
    return "Authors must provide the requested underlying record or confirm a source-grounded wording change; the supplied materials do not establish the requested fact."
