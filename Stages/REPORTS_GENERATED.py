"""
Stage — REPORTS_GENERATED: Multi-audience intelligence briefings via LLM → reports/.
Deterministic: directory setup, prompt construction, file writing.
LLM: natural language report synthesis from structured data.
"""
from __future__ import annotations
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.llm_client import llm_call, parse_json_response

logger = logging.getLogger(__name__)
REPORTS_DIR = "reports"

DISCLAIMER = (
    "\n\n---\n"
    "*This is market intelligence tooling only and must not be presented as financial advice.*"
)

SYSTEM_TRADER_BRIEF = """You are generating a Trader Brief from financial intelligence data.

Style: concise, action-oriented. Emphasise price levels, directional signals, and momentum. 300-500 words. Use bullet points for key signals.

STRICT ANTI-HALLUCINATION RULES:
1. Ground EVERY claim in the entity sentiment and content data provided below.
2. Do NOT invent prices, figures, or market positions not in the source data.
3. If data is insufficient, state that explicitly. Do not fill gaps with assumptions.
4. Cite entity names and sentiment scores from the provided data."""

SYSTEM_ANALYST_REPORT = """You are generating an Analyst Report from financial intelligence data.

Style: detailed, analytical. Include cross-source evidence, confidence scores, and conflicting signals. 800-1200 words. Cite source URLs.

STRICT ANTI-HALLUCINATION RULES:
1. Ground EVERY claim in the entity sentiment and content data provided.
2. Flag where sources disagree — do not hide conflicts.
3. Do NOT invent data not present in the source material.
4. Cite specific content_ids or source_urls for each claim."""

SYSTEM_EXECUTIVE_SUMMARY = """You are generating an Executive Summary from financial intelligence data.

Style: high-level macro overview, key risks, strategic outlook. 300-500 words. Plain language for non-technical readers.

STRICT ANTI-HALLUCINATION RULES:
1. Ground EVERY claim in the provided entity sentiment and content data.
2. Do NOT invent macro trends not supported by the data.
3. Focus on what the data actually shows, not what might typically be expected."""

REPORT_CONFIGS = [
    ("trader_brief.md", SYSTEM_TRADER_BRIEF, "Generate a Trader Brief."),
    ("analyst_report.md", SYSTEM_ANALYST_REPORT, "Generate a detailed Analyst Report."),
    ("executive_summary.md", SYSTEM_EXECUTIVE_SUMMARY, "Generate an Executive Summary."),
]


def generate_reports(
    entities: list[dict],
    sentiments: list[dict],
    qa_issues: list[dict],
    content_items: list[dict],
) -> list[str]:
    """
    Generate 3 report variants from intelligence data.
    Each is a separate LLM call with its own log entry.
    Returns list of successfully written report paths.
    """
    os.makedirs(REPORTS_DIR, exist_ok=True)

    user_content = _build_intelligence_payload(
        entities, sentiments, qa_issues, content_items
    )
    written: list[str] = []

    for filename, system_prompt, task_prompt in REPORT_CONFIGS:
        output_path = os.path.join(REPORTS_DIR, filename)
        full_user = f"{task_prompt}\n\n{user_content}"

        try:
            raw = llm_call(
                stage="report_generation",
                system=system_prompt,
                user_content=full_user,
                input_artifacts=[
                    "entities.json", "entity_sentiment.json",
                    "qa_report.json", "extracted_content.json",
                ],
                output_artifact=output_path,
            )

            if not raw or not raw.strip():
                logger.warning(f"[REPORTS] Empty response for {filename}; skipping")
                continue

            report_text = raw.strip() + DISCLAIMER

            with open(output_path, "w", encoding="utf-8") as f:
                f.write(report_text)
            written.append(output_path)
            logger.info(f"[REPORTS] Written → {output_path}")

        except Exception as e:
            logger.error(f"[REPORTS] Failed to generate {filename}: {e}")

    return written


def _build_intelligence_payload(
    entities: list[dict],
    sentiments: list[dict],
    qa_issues: list[dict],
    content_items: list[dict],
) -> str:
    lines = ["## Intelligence Data\n"]

    lines.append(f"### Entities ({len(entities)} resolved)")
    for ent in entities[:25]:
        lines.append(
            f"- {ent['entity_id']} | {ent['canonical_name']} "
            f"({ent.get('entity_type', 'unknown')}) | "
            f"aliases: {', '.join(ent.get('aliases', [])[:3])}"
        )

    lines.append(f"\n### Entity Sentiment Scores ({len(sentiments)} records)")
    sent_map = {s["entity_id"]: s for s in sentiments}
    for s in sentiments[:25]:
        ev_count = len(s.get("evidence", []))
        lines.append(
            f"- {s['canonical_name']}: {s.get('sentiment')} "
            f"(score={s.get('sentiment_score', 0):.2f}, "
            f"conf={s.get('confidence', 0):.2f}, {ev_count} evidence spans)"
        )
        for ev in s.get("evidence", [])[:2]:
            lines.append(f"  • \"{ev.get('source_span', '')}\" — {ev.get('reason', '')}")

    lines.append(f"\n### QA Issues ({len(qa_issues)} total)")
    for issue in qa_issues[:10]:
        lines.append(
            f"- [{issue.get('severity').upper()}] {issue.get('issue_type')}: "
            f"{issue.get('details', '')[:120]}"
        )

    lines.append(f"\n### Source Coverage")
    urls = {c["source_url"] for c in content_items}
    for url in sorted(urls):
        count = sum(1 for c in content_items if c["source_url"] == url)
        lines.append(f"- {url} ({count} items)")

    return "\n".join(lines)
