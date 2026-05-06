"""
Stage — QA_AND_CONFLICTS_CHECKED: LLM QA + conflict detection → qa_report.json.
Also: code-based low-confidence entity flagging (deterministic).
Deterministic: low-confidence check, Pydantic validation, issue ID generation.
LLM: conflict reasoning and issue classification.
"""
from __future__ import annotations
import json
import logging
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.llm_client import llm_call, parse_json_response
from utils.models import QAIssue

logger = logging.getLogger(__name__)
OUTPUT_PATH = "qa_report.json"
LOW_CONFIDENCE_THRESHOLD = 0.6

SYSTEM_QA_CONFLICTS = """You are a financial intelligence QA engine. Review extracted content, entities, and sentiment for quality issues and conflicts.

Flag these issue types:
- conflicting_sentiment: same entity rated bullish by one source, bearish by another
- numerical_conflict: same metric reported differently across sources
- ungrounded_claim: sentiment claim with no cited source span
- duplicate_content: near-identical content from different sources
- other: any other quality concern

Severity: critical | warning | info

For each issue, cite the entity_ids and content_ids involved.

STRICT RULES:
1. Only reference entity_ids and content_ids that exist in the provided data.
2. Do NOT invent conflicts not supported by the data.
3. Cite specific evidence for each issue flagged.

Output valid JSON only. No markdown fences.
Schema: {"qa_issues":[{"issue_id":"string","severity":"critical|warning|info","issue_type":"string","entities":["entity_id"],"source_content_ids":["content_id"],"details":"string"}]}"""


def run_qa_and_conflicts(
    entities: list[dict],
    sentiments: list[dict],
    content_items: list[dict],
) -> list[dict]:
    """
    1. Code-based: flag low-confidence entities (deterministic).
    2. LLM-based: detect conflicts and quality issues.
    Returns merged list of QAIssue dicts.
    """
    all_issues: list[dict] = []

    # Deterministic low-confidence flagging (code-enforced, not LLM)
    low_conf_issues = _flag_low_confidence(entities)
    all_issues.extend(low_conf_issues)
    logger.info(
        f"[QA] {len(low_conf_issues)} low-confidence entity issues flagged by code"
    )

    # LLM-based conflict detection
    valid_entity_ids = {e["entity_id"] for e in entities}
    valid_content_ids = {c["content_id"] for c in content_items}
    user_content = _build_user_content(entities, sentiments, content_items)

    try:
        raw_response = llm_call(
            stage="qa_and_conflict_detection",
            system=SYSTEM_QA_CONFLICTS,
            user_content=user_content,
            input_artifacts=[
                "entities.json", "entity_sentiment.json", "extracted_content.json"
            ],
            output_artifact=OUTPUT_PATH,
        )
        parsed = parse_json_response(raw_response)
        raw_issues = (
            parsed.get("qa_issues", []) if isinstance(parsed, dict) else []
        )
        llm_issues = _validate_issues(raw_issues, valid_entity_ids, valid_content_ids)
        all_issues.extend(llm_issues)
        logger.info(f"[QA] {len(llm_issues)} LLM-detected issues")
    except Exception as e:
        logger.error(f"[QA] LLM QA call failed: {e}. Proceeding with code-only issues.")

    _write(all_issues)
    return all_issues


def _flag_low_confidence(entities: list[dict]) -> list[dict]:
    """Code-enforced: any entity below threshold gets a QA issue."""
    issues = []
    for ent in entities:
        if ent.get("resolution_confidence", 1.0) < LOW_CONFIDENCE_THRESHOLD:
            issue_id = f"QA_{uuid.uuid4().hex[:6].upper()}"
            issues.append({
                "issue_id": issue_id,
                "severity": "warning",
                "issue_type": "unresolved_entity",
                "entities": [ent["entity_id"]],
                "source_content_ids": [
                    m["content_id"]
                    for m in ent.get("source_mentions", [])
                ],
                "details": (
                    f"Entity '{ent.get('canonical_name')}' has low resolution confidence "
                    f"({ent.get('resolution_confidence', 0):.2f} < {LOW_CONFIDENCE_THRESHOLD}). "
                    "Requires manual review."
                ),
            })
    return issues


def _validate_issues(
    raw: list[dict], valid_eids: set[str], valid_cids: set[str]
) -> list[dict]:
    validated = []
    for raw_issue in raw:
        # Filter to valid entity/content IDs only
        raw_issue["entities"] = [
            eid for eid in raw_issue.get("entities", []) if eid in valid_eids
        ]
        raw_issue["source_content_ids"] = [
            cid for cid in raw_issue.get("source_content_ids", [])
            if cid in valid_cids
        ]
        if not raw_issue.get("issue_id"):
            raw_issue["issue_id"] = f"QA_{uuid.uuid4().hex[:6].upper()}"
        try:
            issue = QAIssue(**raw_issue)
            validated.append(issue.model_dump())
        except Exception as e:
            logger.warning(f"[QA] Invalid issue {raw_issue.get('issue_id')}: {e}")
    return validated


def _build_user_content(
    entities: list[dict], sentiments: list[dict], content_items: list[dict]
) -> str:
    lines = ["Review the following data for QA issues and conflicts.\n"]
    lines.append(f"## Entities ({len(entities)} total)")
    for e in entities[:30]:
        lines.append(
            f"- {e['entity_id']}: {e['canonical_name']} "
            f"(confidence={e.get('resolution_confidence', 0):.2f})"
        )

    lines.append(f"\n## Entity Sentiments ({len(sentiments)} total)")
    for s in sentiments[:30]:
        lines.append(
            f"- {s['entity_id']} ({s['canonical_name']}): "
            f"{s.get('sentiment')} score={s.get('sentiment_score', 0):.2f} "
            f"conf={s.get('confidence', 0):.2f} "
            f"evidence_count={len(s.get('evidence', []))}"
        )

    lines.append(f"\n## Source URLs seen")
    seen_urls = {c["source_url"] for c in content_items}
    for url in sorted(seen_urls):
        lines.append(f"- {url}")

    return "\n".join(lines)


def _write(issues: list[dict]) -> None:
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(issues, f, ensure_ascii=False, indent=2)
    logger.info(f"[QA] Written → {OUTPUT_PATH} ({len(issues)} issues)")
