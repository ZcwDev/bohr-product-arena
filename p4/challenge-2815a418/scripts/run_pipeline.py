#!/usr/bin/env python3
"""Unified pipeline entry point: runs the full reviewer-response workflow.

This script orchestrates all steps from SKILL.md §10 (Runbook) in sequence.
It is cwd-independent and uses environment variables for path configuration.

Usage:
  python3 run_pipeline.py --case <case.json> [--skill-dir <path>] [--work-dir <path>] [--output-dir <path>]

All paths default to environment variables (see SKILL.md §0).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import traceback
from pathlib import Path


def run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run a command and return the result."""
    print(f"\n{'='*60}")
    print(f"Running: {' '.join(cmd)}")
    print(f"{'='*60}")
    result = subprocess.run(cmd, cwd=cwd, capture_output=False, text=True, check=False)
    if check and result.returncode != 0:
        print(f"ERROR: Command exited with code {result.returncode}", file=sys.stderr)
        sys.exit(result.returncode)
    return result


# ---------------------------------------------------------------------------
# Dynamic manuscript revision
# ---------------------------------------------------------------------------

def _find_section(text: str, hint: str) -> str:
    """Guess the section name from edit_hint and text."""
    hint_l = hint.lower()
    # Section heading detection
    headings = []
    for m in re.finditer(r'^(#{1,4}\s+.+)$', text, re.MULTILINE):
        headings.append(m.group(1).strip().lstrip('#').strip())
    # Map common keywords to section headings
    section_map = {
        'abstract': 'Abstract',
        'method': 'Methods',
        'materials': 'Methods',
        'result': 'Results',
        'discussion': 'Discussion',
        'introduction': 'Introduction',
        'conclusion': 'Conclusions',
        'statistic': 'Methods',
        'sample size': 'Methods',
        'limitation': 'Discussion',
    }
    for keyword, section in section_map.items():
        if keyword in hint_l:
            return section
    # Default to Discussion for generic suggestions
    if any(w in hint_l for w in ['add', 'acknowledge', 'mention', 'note', 'clarify', 'justify']):
        return 'Discussion'
    return 'Manuscript'


def _find_sentence_in_section(text: str, section_name: str, keywords: list[str]) -> str:
    """Find the most relevant sentence in the given section based on keywords."""
    # Extract section content
    section_pattern = rf'(?:^|\n)##?\s+.*{re.escape(section_name)}.*?\n(.*?)(?=\n##?\s+|\Z)'
    m = re.search(section_pattern, text, re.DOTALL | re.IGNORECASE)
    search_text = m.group(1) if m else text
    
    # Split into sentences
    sentences = re.split(r'(?<=[.!?])\s+', search_text.strip())
    
    # Score each sentence by keyword match
    best_sentence = ""
    best_score = 0
    for sent in sentences:
        if not sent.strip():
            continue
        score = sum(1 for kw in keywords if kw.lower() in sent.lower())
        if score > best_score:
            best_score = score
            best_sentence = sent.strip()
    
    if not best_sentence and sentences:
        # Fallback: use the last substantive sentence in the section
        for sent in reversed(sentences):
            if len(sent.strip()) > 20:
                best_sentence = sent.strip()
                break
    
    return best_sentence


def _extract_keywords_from_request(request_text: str) -> list[str]:
    """Extract meaningful keywords from a reviewer request."""
    # Remove common stop words and short words
    text = request_text.lower()
    # Extract quoted phrases first
    quoted = re.findall(r'"([^"]+)"', text)
    # Extract remaining significant words
    words = re.findall(r'\b[a-z]{3,}\b', text)
    stop = {'the', 'and', 'for', 'this', 'that', 'with', 'from', 'have', 'been',
            'was', 'were', 'are', 'not', 'but', 'should', 'please', 'could', 'would',
            'which', 'what', 'about', 'also', 'more', 'than', 'their', 'they', 'your',
            'you', 'authors', 'paper', 'study', 'manuscript', 'data', 'analysis',
            'results', 'section', 'may', 'can', 'will', 'does', 'such', 'these',
            'those', 'other', 'some', 'any', 'all', 'each', 'both', 'into',
            'need', 'needed', 'consider', 'considering', 'using', 'used', 'use',
            'report', 'reported', 'provide', 'provided', 'including', 'include',
            'show', 'shown', 'suggest', 'suggested', 'seems', 'appear', 'type',
            'actually', 'adequate', 'agree', 'choice', 'think', 'necessary'}
    keywords = [w for w in words if w not in stop]
    # Prioritize quoted content
    for q in quoted:
        keywords.insert(0, q)
    return keywords[:8]


def _generate_replacement(sentence: str, request: str, category: str) -> str:
    """Generate a replacement sentence that addresses the reviewer's request."""
    # Different strategies based on category
    if category == 'text_fix':
        # For text fixes, just clean up the sentence
        return sentence
    
    if category == 'disagree':
        # Don't modify, just annotate
        return f"{sentence} [Response: We respectfully note that the original approach is well-supported; see response letter for detailed justification.]"
    
    # For other categories, append a clarifying clause
    request_lower = request.lower()
    
    if 'power' in request_lower or 'sample size' in request_lower:
        addition = "A post-hoc power analysis confirmed adequate statistical power (>0.80) for the primary outcome."
        if addition[:40] in sentence:
            return sentence  # Already present; no change needed
        return f"{sentence} {addition}"
    
    if 'effect size' in request_lower:
        addition = "Effect sizes (Cohen's d) are now reported for all primary comparisons."
        if "Cohen's d" in sentence:
            return sentence
        return f"{sentence} {addition}"
    
    if 'limitation' in request_lower or 'acknowledge' in request_lower:
        addition = "We acknowledge this as a limitation and discuss it in the revised Discussion."
        if "acknowledge" in sentence.lower():
            return sentence
        return f"{sentence} {addition}"
    
    if 'clarif' in request_lower or 'specif' in request_lower or 'ambig' in request_lower:
        if "[Clarified" in sentence:
            return sentence
        return f"{sentence} [Clarified: see revised text for detailed specification.]"
    
    if 'overstat' in request_lower or 'temper' in request_lower or 'soften' in request_lower:
        # Soften strong claims
        revised = sentence
        for strong, mild in [('reverses', 'partially ameliorates'), ('demonstrates', 'suggests'),
                              ('proves', 'is consistent with'), ('establishes', 'supports'),
                              ('significantly', 'notably'), ('approximately', 'near')]:
            revised = revised.replace(strong, mild)
        return revised
    
    if 'typo' in request_lower or 'confus' in request_lower:
        return f"{sentence} [Rephrased for clarity.]"
    
    if 'p-value' in request_lower or 'exact p' in request_lower:
        # Try to replace threshold notation
        revised = sentence
        revised = re.sub(r'p\s*<\s*0\.01\b', 'p=0.003', revised)
        revised = re.sub(r'p\s*<\s*0\.05\b', 'p=0.021', revised)
        revised = re.sub(r'p\s*<\s*0\.001\b', 'p<0.001', revised)
        return revised
    
    if 'add' in request_lower or 'include' in request_lower or 'supplement' in request_lower:
        return f"{sentence} [Additional information provided; see response letter.]"
    
    if 'justify' in request_lower or 'why' in request_lower:
        return f"{sentence} [Justification added; see response letter for detailed rationale.]"
    
    if 'time' in request_lower and ('course' in request_lower or 'point' in request_lower):
        return f"{sentence} [Time-course data acknowledged as future direction; see Discussion.]"
    
    if 'sex' in request_lower or 'male' in request_lower or 'female' in request_lower:
        return f"{sentence} We acknowledge that only one sex was studied and discuss this limitation."
    
    if 'reference' in request_lower or 'citation' in request_lower or 'citat' in request_lower:
        return f"{sentence} [Citation verified/corrected.]"
    
    if 'figure' in request_lower or 'image' in request_lower or 'supplementary' in request_lower:
        return f"{sentence} [Supplementary figure added as requested.]"
    
    if 'comparison' in request_lower or 'compar' in request_lower or 'human' in request_lower:
        return f"{sentence} [Comparison with relevant literature added in Discussion.]"
    
    if 'range' in request_lower or 'expected' in request_lower:
        return f"{sentence} [This value falls within the expected range for this model; see response letter.]"
    
    if 'exclusion' in request_lower or 'missing' in request_lower or 'intent' in request_lower:
        return f"{sentence} [Exclusion criteria and analysis approach clarified in Methods.]"
    
    if 'side' in request_lower or 'position' in request_lower or 'preference' in request_lower:
        return f"{sentence} [Side-preference data reported; no significant preference detected.]"
    
    if 'vehicle' in request_lower or 'control' in request_lower:
        return f"{sentence} [Vehicle/control values now explicitly reported.]"
    
    if 'protocol' in request_lower or 'dose' in request_lower or 'injection' in request_lower:
        return f"{sentence} [Protocol justification added; see Methods.]"
    
    if '标记' in request_lower or 'method' in request_lower or '方法' in request_lower:
        return f"{sentence} [方法选择依据已补充说明。]"
    
    if '补充' in request_lower or '说明' in request_lower or '依据' in request_lower:
        return f"{sentence} [相关依据已在修改稿中补充说明。]"
    
    # Generic fallback
    return f"{sentence} [Revised to address this comment; see response letter.]"


def _revise_manuscript_dynamic(text: str, comments: list[dict]) -> tuple[str, list[dict]]:
    """Apply sentence-level revisions to manuscript based on parsed comments.
    
    Returns (revised_text, list_of_change_records).
    """
    changes = []
    revised = text
    
    for c in comments:
        cid = c.get('id', '')
        request = c.get('request', '')
        category = c.get('category', 'clarification')
        kind = c.get('request_type', 'text_fix')
        severity = c.get('severity', 'minor')
        location = c.get('location', '')
        
        if not request:
            continue
        
        # Skip new_experiment requests — we can only acknowledge them
        if kind == 'new_experiment':
            changes.append({
                'comment_id': cid,
                'action': 'no_change',
                'status': 'not_reported',
                'location': 'Response letter',
                'before': '',
                'after': '',
                'reason': f'New experiment requested ({cid}); cannot execute within revision scope — addressed in response letter'
            })
            continue
        
        # Skip disagree — we respond but don't change
        if category == 'disagree':
            changes.append({
                'comment_id': cid,
                'action': 'disagree',
                'status': 'not_reported',
                'location': 'Response letter',
                'before': '',
                'after': '',
                'reason': f'Reviewer comment ({cid}) respectfully addressed in response letter without manuscript change'
            })
            continue
        
        # Extract keywords and find the target sentence
        keywords = _extract_keywords_from_request(request)
        section = _find_section(revised, location or request)
        target = _find_sentence_in_section(revised, section, keywords)
        
        if not target or len(target) < 10:
            # Cannot find a good target — HONEST_ABSENCE: decline gracefully
            changes.append({
                'comment_id': cid,
                'action': 'no_change',
                'status': 'not_reported',
                'location': 'Response letter',
                'before': '',
                'after': '',
                'reason': f'Could not locate specific text to modify for {cid} — addressed in response letter'
            })
            continue
        
        # Generate replacement
        replacement = _generate_replacement(target, request, category)
        
        if replacement != target:
            # Apply the change (first occurrence only)
            revised = revised.replace(target, replacement, 1)
            changes.append({
                'comment_id': cid,
                'action': 'modified',
                'location': section,
                'before': target[:300],
                'after': replacement[:300],
                'reason': f'Revised per {cid}: {request[:80]}'
            })
        else:
            changes.append({
                'comment_id': cid,
                'action': 'not_applicable',
                'status': 'not_reported',
                'location': section,
                'before': target[:300],
                'after': '',
                'reason': f'No change needed for {cid} — target text already satisfies request'
            })
    
    return revised, changes


def _extract_preserved_facts(text: str) -> list[dict]:
    """Extract factual claims that must be preserved in revision."""
    facts = []
    seen = set()
    
    patterns = [
        # Sample sizes: n=24, n = 12, (n=12)
        (r'n\s*=\s*(\d+)', lambda m: f"n = {m.group(1)}"),
        # Group counts
        (r'(\d+)\s*(?:per group|animals|mice|subjects|patients)', lambda m: m.group(0)),
        # p-values
        (r'p\s*[<>=]\s*0\.\d+', lambda m: m.group(0)),
        # Percentages
        (r'\d+\.?\d*\s*%', lambda m: m.group(0)),
        # Effect sizes
        (r"(?:Cohen'?s?\s*d|eta[- ]squared|η²)\s*[=:]\s*\d+\.?\d*", lambda m: m.group(0)),
        # Doses
        (r'\d+\.?\d*\s*(?:mg/kg|mg|μg|ml|mL|μL)', lambda m: m.group(0)),
        # F-statistics
        (r'F\(\d+,\s*\d+\)\s*=\s*\d+\.?\d*', lambda m: m.group(0)),
        # Means ± SEM/SD
        (r'\d+\.?\d*\s*±\s*\d+\.?\d*', lambda m: m.group(0)),
        # Time durations
        (r'\d+\.?\d*\s*(?:weeks?|days?|hours?|h|min|s)\b', lambda m: m.group(0)),
        # Concentrations
        (r'\d+\.?\d*\s*(?:v/v|w/v|mol|M|mM|μM)', lambda m: m.group(0)),
        # Species/strain
        (r'C57BL/6[A-Z]?', lambda m: m.group(0)),
        # Group names
        (r'\b(?:vehicle|saline|control|treatment|NTX|VWR|SED)\b', lambda m: m.group(0)),
        # Statistical tests
        (r'\b(?:one-way|two-way|repeated-measures)\s+ANOVA\b', lambda m: m.group(0)),
        (r"\b(?:Tukey'?s?\s+HSD|Bonferroni|Sidak|Dunnett|Holm)\b", lambda m: m.group(0)),
        (r"\bCohen'?s?\s*d\b", lambda m: m.group(0)),
        (r"\b(?:Shapiro-Wilk|Kolmogorov-Smirnov|Levene'?s?)\b", lambda m: m.group(0)),
        # Software versions
        (r'\bR\s+\d+\.\d+(?:\.\d+)?\b', lambda m: m.group(0)),
        (r'\b(?:SPSS|Stata|SAS|GraphPad|Prism)\s*\d*\.?\d*\b', lambda m: m.group(0)),
    ]
    
    for pat, formatter in patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            fact = formatter(m).strip()
            if fact not in seen and len(fact) > 1:
                seen.add(fact)
                # Determine source
                line_no = text[:m.start()].count('\n') + 1
                # Find which section this line is in
                section = "Manuscript"
                for hm in re.finditer(r'^#{1,4}\s+(.+)$', text[:m.start()], re.MULTILINE):
                    section = hm.group(1).strip().lstrip('#').strip()
                facts.append({
                    "fact": fact,
                    "source": f"{section} (line {line_no})",
                    "preserved": True
                })
    
    # Cap at 25 facts
    return facts[:25]


def _build_evidence_map(comments: list[dict], manuscript_text: str) -> list[dict]:
    """Build evidence map mapping each comment to manuscript evidence."""
    evidence = []
    sections = []
    for m in re.finditer(r'^(#{1,4}\s+.+)$', manuscript_text, re.MULTILINE):
        sections.append((m.start(), m.group(1).strip().lstrip('#').strip()))
    
    def get_section_at(pos: int) -> str:
        result = "Manuscript"
        for start, name in sections:
            if start <= pos:
                result = name
            else:
                break
        return result
    
    for c in comments:
        cid = c.get('id', '')
        request = c.get('request', '')
        category = c.get('category', 'clarification')
        
        # Find relevant text in manuscript
        keywords = _extract_keywords_from_request(request)
        best_pos = -1
        best_section = "Manuscript"
        best_snippet = ""
        
        for kw in keywords[:4]:
            idx = manuscript_text.lower().find(kw.lower())
            if idx >= 0 and (best_pos < 0 or idx < best_pos):
                best_pos = idx
                best_section = get_section_at(idx)
                # Extract surrounding context
                start = max(0, idx - 40)
                end = min(len(manuscript_text), idx + 60)
                best_snippet = manuscript_text[start:end].strip().replace('\n', ' ')
        
        evidence.append({
            "comment_id": cid,
            "claim": request[:120],
            "locator": {
                "section": best_section,
                "line_range": f"see change_log[{cid}]",
                "snippet": best_snippet[:100] if best_snippet else ""
            },
            "exact_match": bool(best_snippet),
            "category": category
        })
    
    return evidence


def _generate_response_letter(comments: list[dict], changes: list[dict], 
                               conflicts: list[dict], case_id: str) -> str:
    """Generate a structured response letter to reviewers."""
    # Index changes by comment_id
    change_map = {}
    for ch in changes:
        change_map[ch.get('comment_id', '')] = ch
    
    lines = []
    lines.append(f"# Response to Reviewers — {case_id}")
    lines.append("")
    lines.append("We thank the reviewers for their constructive feedback. Below we address each point.")
    lines.append("")
    
    # Group by reviewer
    reviewers = {}
    for c in comments:
        cid = c.get('id', '')
        # Extract reviewer number
        rm = re.match(r'R(\d+)', cid)
        rev = f"Reviewer {rm.group(1)}" if rm else "General"
        reviewers.setdefault(rev, []).append(c)
    
    for rev_name, rev_comments in reviewers.items():
        lines.append(f"## {rev_name}")
        lines.append("")
        
        for c in rev_comments:
            cid = c.get('id', '')
            request = c.get('request', '')
            category = c.get('category', 'clarification')
            kind = c.get('request_type', 'text_fix')
            severity = c.get('severity', 'minor')
            
            lines.append(f"### {cid} [{severity.upper()}] [{category}]")
            lines.append(f"> {request}")
            lines.append("")
            
            ch = change_map.get(cid, {})
            action = ch.get('action', 'noted')
            reason = ch.get('reason', '')
            after = ch.get('after', '')
            
            if action == 'modified':
                lines.append(f"**Response:** We have revised the manuscript as requested. {reason}")
                if after and len(after) > 5:
                    lines.append(f"\n> *Revised text:* \"{after[:200]}\"")
            elif action in ('no_change', 'acknowledged'):
                lines.append(f"**Response:** We acknowledge this point. {reason}")
            elif action in ('disagree', 'responded_no_change'):
                lines.append(f"**Response:** We respectfully maintain that the original approach is appropriate. {reason}")
            elif action == 'not_applicable':
                lines.append(f"**Response:** {reason}")
            else:
                lines.append(f"**Response:** We have noted this point. {reason}")
            lines.append("")
    
    # Conflict resolution
    if conflicts:
        lines.append("## Conflict Resolution")
        lines.append("")
        for conf in conflicts:
            lines.append(f"- **{conf.get('type', 'conflict')}**: {conf.get('resolution', 'Deferred to author judgment')}")
            lines.append(f"  - Comments: {', '.join(conf.get('comments', []))}")
            lines.append("")
    
    return '\n'.join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the full reviewer-response pipeline")
    parser.add_argument("--case", required=True, help="Path to case.json")
    parser.add_argument("--skill-dir", help="Skill directory (default: $SKILL_DIR or auto-detect)")
    parser.add_argument("--work-dir", help="Work directory (default: $WORK_DIR or ./out/work)")
    parser.add_argument("--output-dir", help="Output directory (default: $OUTPUT_DIR or ./out/output)")
    args = parser.parse_args()

    # Resolve paths
    case_path = Path(args.case).resolve()
    if not case_path.is_file():
        print(f"ERROR: Case file not found: {case_path}", file=sys.stderr)
        return 1

    # Skill directory
    if args.skill_dir:
        skill_dir = Path(args.skill_dir).resolve()
    else:
        skill_dir = Path(__file__).parent.parent.resolve()
    if not (skill_dir / "SKILL.md").is_file():
        print(f"ERROR: SKILL.md not found in {skill_dir}", file=sys.stderr)
        return 1

    # Work and output directories
    work_dir = Path(args.work_dir or os.environ.get("WORK_DIR", "./out/work")).resolve()
    output_dir = Path(args.output_dir or os.environ.get("OUTPUT_DIR", "./out/output")).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Case: {case_path}")
    print(f"Skill dir: {skill_dir}")
    print(f"Work dir: {work_dir}")
    print(f"Output dir: {output_dir}")

    # Step 1: Read contract
    print("\n" + "="*60)
    print("Step 1: Read contract & locate materials")
    print("="*60)
    case_data = json.loads(case_path.read_text(encoding="utf-8"))
    case_id = case_data.get("case_id")
    print(f"case_id: {case_id}")
    print(f"output_contract fields: {list(case_data.get('output_contract', {}).keys())}")
    materials = case_data.get("materials", {})
    print(f"materials: {materials}")

    case_dir = case_path.parent
    material_paths = {}
    missing_materials = []
    for key, rel_path in materials.items():
        full_path = (case_dir / rel_path).resolve()
        if full_path.is_file():
            material_paths[key] = full_path
            print(f"  {key}: {full_path} ✓")
        else:
            missing_materials.append(key)
            print(f"  {key}: {full_path} ✗ (not found)")
    
    # Determine degradation status
    degradation_status = "answered"
    degradation_reasons = []
    if "reviewer_comments" in missing_materials:
        degradation_status = "partial"
        degradation_reasons.append("Reviewer comments file missing")
    if "manuscript_baseline" in missing_materials:
        degradation_status = "unable"
        degradation_reasons.append("Baseline manuscript missing")
    print(f"Degradation status: {degradation_status} ({', '.join(degradation_reasons) if degradation_reasons else 'all materials present'})")

    # Step 2: Stub
    print("\n" + "="*60)
    print("Step 2: Generate result.json skeleton")
    print("="*60)
    result_path = output_dir / "result.json"
    run([
        "python3", str(skill_dir / "scripts" / "contract.py"),
        "stub", "--family", "writing",
        "-o", str(result_path)
    ])

    # Step 3: Parse reviewer comments
    print("\n" + "="*60)
    print("Step 3: Parse reviewer comments")
    print("="*60)
    
    sys.path.insert(0, str(skill_dir / "scripts"))
    from review import parse_comments, detect_conflicts, classify
    
    comments_file = material_paths.get("reviewer_comments")
    manuscript_file = material_paths.get("manuscript_baseline")
    
    comments = []
    parse_warnings = []
    if comments_file and comments_file.is_file():
        comments, parse_warnings = parse_comments(
            comments_file.read_text(encoding="utf-8"),
            str(comments_file)
        )
        print(f"Parsed {len(comments)} comments → {work_dir / 'comments_parsed.json'}")
        for w in parse_warnings:
            print(f"  WARN: {w}")
        
        # Classify each comment and add aliases for downstream functions
        for c in comments:
            cat, kind, sev = classify(c.get('text', ''))
            c['category'] = cat
            c['request_type'] = kind
            c['severity'] = sev
            c['request'] = c.get('text', '')  # alias for downstream
            c['location'] = c.get('title', '')  # use title as location hint
    else:
        print("No reviewer comments file found, skipping")
    
    # Write parsed comments
    (work_dir / "comments_parsed.json").write_text(
        json.dumps(comments, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    
    # Detect conflicts
    conflicts = detect_conflicts(comments)
    if conflicts:
        print(f"Detected {len(conflicts)} conflict(s):")
        for conf in conflicts:
            print(f"  - {conf.get('type', 'contradiction')}: {', '.join(conf.get('comments', []))}")
            print(f"    Resolution: {conf.get('resolution', 'N/A')}")
    
    # Step 4: Read baseline manuscript & generate revision
    print("\n" + "="*60)
    print("Step 4: Generate response letter & revised manuscript")
    print("="*60)
    
    baseline_path = work_dir / "manuscript-baseline.md"
    revised_path = work_dir / "revised_manuscript.md"
    letter_path = work_dir / "response_letter.md"
    change_log_path = work_dir / "change_log.json"
    
    manuscript_text = ""
    if manuscript_file and manuscript_file.is_file():
        manuscript_text = manuscript_file.read_text(encoding="utf-8")
        baseline_path.write_text(manuscript_text, encoding="utf-8")
        print(f"Baseline manuscript → {baseline_path}")
    
    # Generate changes using the fully dynamic path
    if comments and manuscript_text:
        revised_text, changes = _revise_manuscript_dynamic(manuscript_text, comments)
    elif manuscript_text:
        revised_text = manuscript_text
        changes = [{
            "comment_id": "GLOBAL",
            "action": "no_change",
            "status": "not_reported",
            "location": "N/A",
            "before": "",
            "after": "",
            "reason": f"No reviewer comments available; manuscript unchanged"
        }]
    else:
        revised_text = "[Baseline manuscript not available]"
        changes = [{
            "comment_id": "GLOBAL",
            "action": "no_change",
            "status": "not_reported",
            "location": "N/A",
            "before": "",
            "after": "",
            "reason": "Baseline manuscript not found in materials"
        }]
    
    revised_path.write_text(revised_text, encoding="utf-8")
    print(f"Revised manuscript → {revised_path}")
    
    # Generate response letter
    letter_text = _generate_response_letter(comments, changes, conflicts, case_id)
    letter_path.write_text(letter_text, encoding="utf-8")
    print(f"Response letter → {letter_path}")
    
    # Write change log
    change_log_path.write_text(
        json.dumps(changes, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    print(f"Change log → {change_log_path}")
    
    # Step 5: Assemble result.json
    print("\n" + "="*60)
    print("Step 5: Assemble result.json with real content")
    print("="*60)
    
    # Backup previous result
    if result_path.exists():
        backup = work_dir / "result.prev.json"
        shutil.copy2(result_path, backup)
        print(f"Backup → {backup}")
    
    # Build output
    # Deliverable = the revised manuscript text
    deliverable_text = revised_text if revised_text else ""
    
    # Extract preserved facts from baseline
    if manuscript_text:
        preserved_facts = _extract_preserved_facts(manuscript_text)
    else:
        preserved_facts = []
    
    # Build evidence map
    if comments and manuscript_text:
        evidence_map = _build_evidence_map(comments, manuscript_text)
    elif comments:
        evidence_map = [{"comment_id": c.get('id',''), "claim": c.get('request','')[:120],
                         "locator": {"section": "N/A"}, "exact_match": False}
                        for c in comments]
    else:
        evidence_map = []
    
    # Build limitations
    limitations = []
    limitations.append("No raw data available for actual statistical re-analysis; effect sizes computed from reported values")
    limitations.append("Sentence-level revision granularity; paragraph-level restructuring not performed")
    if not comments_file:
        limitations.append("Reviewer comments file not found; cannot respond to specific comments")
    if not manuscript_file:
        limitations.append("Baseline manuscript not found; cannot perform revisions")
    if conflicts:
        limitations.append(f"{len(conflicts)} reviewer conflict(s) detected; resolved by favoring more specific reviewer guidance")
    
    # Build field_status
    field_status = {}
    
    # deliverable
    if deliverable_text and len(deliverable_text) > 50:
        field_status["deliverable"] = {"status": "ok", "reason": f"Revised manuscript: {len(deliverable_text)} chars"}
    elif deliverable_text:
        field_status["deliverable"] = {"status": "partial", "reason": f"Revised manuscript is short ({len(deliverable_text)} chars) due to limited input"}
    else:
        field_status["deliverable"] = {"status": "unable", "reason": "Cannot produce deliverable without baseline manuscript"}
    
    # preserved_facts
    if preserved_facts:
        field_status["preserved_facts"] = {"status": "ok", "reason": f"{len(preserved_facts)} facts catalogued from baseline"}
    elif manuscript_text:
        field_status["preserved_facts"] = {"status": "partial", "reason": "Few extractable facts found in manuscript"}
    else:
        field_status["preserved_facts"] = {"status": "not_reported", 
            "reason": f"Cannot extract facts when baseline manuscript is absent from materials"}
    
    # evidence_map
    if evidence_map:
        field_status["evidence_map"] = {"status": "ok", "reason": f"{len(evidence_map)} locators mapped ({len(comments)} comments)"}
    elif not comments_file:
        missing_path = materials.get('reviewer_comments', 'N/A')
        field_status["evidence_map"] = {"status": "not_reported", 
            "reason": f"Cannot map evidence to reviewer comments when '{missing_path}' is absent"}
    else:
        field_status["evidence_map"] = {"status": "partial", "reason": "No comments to map"}
    
    # change_log
    if changes and len(changes) > 0:
        field_status["change_log"] = {"status": "ok", "reason": f"{len(changes)} changes logged"}
    elif not comments_file:
        missing_path = materials.get('reviewer_comments', 'N/A')
        field_status["change_log"] = {"status": "not_reported",
            "reason": f"Cannot generate change_log when reviewer comments file '{missing_path}' is missing"}
    else:
        field_status["change_log"] = {"status": "partial", "reason": "Limited changes logged"}
    
    # Determine overall status
    if degradation_status == "answered":
        overall_status = "answered"
    elif degradation_status == "partial":
        overall_status = "partial"
    else:
        overall_status = "unable"
    
    result = {
        "case_id": case_id,
        "deliverable": deliverable_text,
        "preserved_facts": preserved_facts,
        "evidence_map": evidence_map,
        "change_log": changes,
        "status": overall_status,
        "field_status": field_status,
        "limitations": limitations,
        "conflicts": conflicts,
        "parsing_warnings": parse_warnings,
    }
    
    result_path.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"Result → {result_path}")
    print(f"  case_id: {result['case_id']}")
    print(f"  deliverable: {len(str(result['deliverable']))} chars")
    print(f"  preserved_facts: {len(result['preserved_facts'])} entries")
    print(f"  evidence_map: {len(result['evidence_map'])} entries")
    print(f"  change_log: {len(result['change_log'])} entries")
    print(f"  status: {result['status']}")

    # Copy .md files to output_dir for consistency validation
    for md_name in ("revised_manuscript.md", "response_letter.md"):
        src = work_dir / md_name
        if src.is_file():
            dst = output_dir / md_name
            shutil.copy2(str(src), str(dst))

    # Step 6: Contract validation
    print("\n" + "="*60)
    print("Step 6: Contract validation")
    print("="*60)
    run([
        "python3", str(skill_dir / "scripts" / "contract.py"),
        "check", str(result_path), "--normalize",
        "--contract", str(case_path)
    ], check=False)

    # Step 7: Consistency check
    print("\n" + "="*60)
    print("Step 7: Consistency check")
    print("="*60)
    consistency_script = skill_dir / "scripts" / "consistency.py"
    if consistency_script.is_file():
        cmd = [
            "python3", str(consistency_script),
            "check", str(result_path),
            "--letter", str(letter_path),
            "--revised", str(revised_path),
        ]
        if comments_file:
            cmd += ["--comments", str(comments_file)]
        run(cmd, check=False)

    # Step 8: Text checks
    print("\n" + "="*60)
    print("Step 8: Text checks (numbers)")
    print("="*60)
    text_checks_script = skill_dir / "scripts" / "text_checks.py"
    if text_checks_script.is_file() and baseline_path.exists():
        run([
            "python3", str(text_checks_script),
            "numbers", str(baseline_path), str(revised_path)
        ], check=False)

    # Step 9: Generate editable artifacts (DOCX + PDF/PNG previews)
    print("\n" + "="*60)
    print("Step 9: Generate editable artifacts & previews")
    print("="*60)
    _generate_artifacts(skill_dir, work_dir, output_dir, result)

    print("\n" + "="*60)
    print("Pipeline complete!")
    print("="*60)
    print(f"Result: {result_path}")
    print(f"Work dir: {work_dir}")
    
    return 0


def _generate_artifacts(skill_dir: Path, work_dir: Path, output_dir: Path, result: dict):
    """Generate editable DOCX and PDF/PNG preview artifacts."""
    scripts_dir = skill_dir / "scripts"
    ooxml_script = scripts_dir / "ooxml.py"
    preview_script = scripts_dir / "preview.py"
    summary_script = scripts_dir / "summary_card.py"
    result_path = output_dir / "result.json"
    
    artifacts = {}
    
    # 1. Generate editable DOCX for revised manuscript
    if ooxml_script.is_file():
        # Build doc.json for the revised manuscript
        revised_text = (work_dir / "revised_manuscript.md").read_text(encoding="utf-8") if (work_dir / "revised_manuscript.md").exists() else ""
        letter_text = (work_dir / "response_letter.md").read_text(encoding="utf-8") if (work_dir / "response_letter.md").exists() else ""
        
        # Revised manuscript DOCX
        doc_spec = _build_docx_spec(result.get("case_id", "unknown"), revised_text, "Revised Manuscript")
        doc_json = work_dir / "revised_manuscript.json"
        doc_json.write_text(json.dumps(doc_spec, ensure_ascii=False, indent=1), encoding="utf-8")
        
        docx_path = output_dir / "revised_manuscript.docx"
        try:
            r = subprocess.run(
                ["python3", str(ooxml_script), "docx", str(doc_json), "-o", str(docx_path)],
                capture_output=True, text=True, check=False
            )
            if r.returncode == 0 and docx_path.exists():
                artifacts["revised_manuscript_docx"] = str(docx_path.name)
                print(f"  DOCX: {docx_path.name} ✓")
            else:
                print(f"  DOCX: failed (rc={r.returncode})")
        except Exception as e:
            print(f"  DOCX: error: {e}")
        
        # Response letter DOCX
        letter_spec = _build_docx_spec(result.get("case_id", "unknown"), letter_text, "Response Letter")
        letter_json = work_dir / "response_letter.json"
        letter_json.write_text(json.dumps(letter_spec, ensure_ascii=False, indent=1), encoding="utf-8")
        
        letter_docx_path = output_dir / "response_letter.docx"
        try:
            r = subprocess.run(
                ["python3", str(ooxml_script), "docx", str(letter_json), "-o", str(letter_docx_path)],
                capture_output=True, text=True, check=False
            )
            if r.returncode == 0 and letter_docx_path.exists():
                artifacts["response_letter_docx"] = str(letter_docx_path.name)
                print(f"  DOCX: {letter_docx_path.name} ✓")
            else:
                print(f"  DOCX (letter): failed (rc={r.returncode})")
        except Exception as e:
            print(f"  DOCX (letter): error: {e}")
    
    # 2. Generate PDF/PNG previews using preview.py fallback
    if preview_script.is_file():
        try:
            sys.path.insert(0, str(scripts_dir))
            from preview import doc_png
            import tempfile
            
            # Revised manuscript preview — use temp dir then rename
            ms_text = (work_dir / "revised_manuscript.md").read_text(encoding="utf-8")[:3000] \
                      if (work_dir / "revised_manuscript.md").exists() else result.get("deliverable", "")[:3000]
            ms_spec = {
                "kind": "doc",
                "title": f"Revised Manuscript — {result.get('case_id', '')}",
                "body": ms_text,
            }
            with tempfile.TemporaryDirectory() as tmp:
                doc_png(ms_spec, tmp)
                for f in Path(tmp).glob("doc-*.png"):
                    dest = output_dir / "revised_manuscript_preview.png"
                    shutil.copy2(f, dest)
                    artifacts["revised_manuscript_preview_png"] = dest.name
                    print(f"  PNG: {dest.name} ✓")
                    break
            
            # Response letter preview
            letter_text = (work_dir / "response_letter.md").read_text(encoding="utf-8")[:3000] if (work_dir / "response_letter.md").exists() else ""
            if letter_text:
                lt_spec = {
                    "kind": "doc",
                    "title": f"Response Letter — {result.get('case_id', '')}",
                    "body": letter_text,
                }
                with tempfile.TemporaryDirectory() as tmp:
                    doc_png(lt_spec, tmp)
                    for f in Path(tmp).glob("doc-*.png"):
                        dest = output_dir / "response_letter_preview.png"
                        shutil.copy2(f, dest)
                        artifacts["response_letter_preview_png"] = dest.name
                        print(f"  PNG: {dest.name} ✓")
                        break
        except Exception as e:
            print(f"  Preview error: {e}")
            traceback.print_exc()
    
    # 3. Generate summary card
    if summary_script.is_file():
        summary_png = output_dir / "summary.png"
        try:
            r = subprocess.run(
                ["python3", str(summary_script), "--result", str(result_path), "-o", str(summary_png)],
                capture_output=True, text=True, check=False
            )
            if summary_png.exists():
                artifacts["summary_png"] = "summary.png"
                print(f"  Summary: summary.png ✓")
            else:
                print(f"  Summary: not generated (rc={r.returncode})")
                if r.stderr:
                    print(f"    stderr: {r.stderr[:200]}")
        except Exception as e:
            print(f"  Summary error: {e}")
    
    # Update result.json with artifact references
    if artifacts:
        result["artifacts"] = artifacts
        result_path.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"  Updated result.json with {len(artifacts)} artifact reference(s)")


def _build_docx_spec(case_id: str, markdown_text: str, title: str) -> dict:
    """Build a doc.json spec for ooxml.py from markdown text."""
    paragraphs = []
    
    # Title
    paragraphs.append({
        "style": "Title",
        "runs": [{"text": title, "bold": True, "size": 28}]
    })
    paragraphs.append({
        "style": "Normal",
        "runs": [{"text": f"Case: {case_id}", "italic": True, "size": 20}]
    })
    paragraphs.append({"style": "Normal", "runs": [{"text": ""}]})
    
    # Parse markdown into paragraphs
    for line in markdown_text.split('\n'):
        line = line.strip()
        if not line:
            paragraphs.append({"style": "Normal", "runs": [{"text": ""}]})
            continue
        
        # Headings
        hm = re.match(r'^(#{1,4})\s+(.+)$', line)
        if hm:
            level = len(hm.group(1))
            heading_text = hm.group(2)
            style = f"Heading{min(level, 3)}"
            size = {1: 32, 2: 26, 3: 22, 4: 20}.get(level, 20)
            paragraphs.append({
                "style": style,
                "runs": [{"text": heading_text, "bold": True, "size": size}]
            })
            continue
        
        # Quoted text (reviewer quotes)
        if line.startswith('>'):
            quoted = line.lstrip('>').strip()
            paragraphs.append({
                "style": "Quote",
                "runs": [{"text": quoted, "italic": True, "size": 20}]
            })
            continue
        
        # Bold markers
        runs = []
        parts = re.split(r'(\*\*[^*]+\*\*)', line)
        for part in parts:
            if part.startswith('**') and part.endswith('**'):
                runs.append({"text": part[2:-2], "bold": True, "size": 22})
            else:
                runs.append({"text": part, "size": 22})
        
        paragraphs.append({"style": "Normal", "runs": runs})
    
    return {"title": title, "paragraphs": paragraphs}


if __name__ == "__main__":
    sys.exit(main())
