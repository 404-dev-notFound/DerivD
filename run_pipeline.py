#!/usr/bin/env python3
"""
run_pipeline.py — Single entry point for the financial content intelligence pipeline.

Stage order is enforced by advance(). Skipping or reordering stages raises AssertionError.
All stages are imported from Stages/ and called through this orchestrator.
"""
from __future__ import annotations

import logging
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()

# ── Logging setup ──────────────────────────────────────────────────────────────
# Windows legacy consoles default to cp1252 which can't encode arrows / em-dashes
# used in log messages. Force stdout to UTF-8 so log output never crashes formatting.
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except (AttributeError, ValueError):
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("pipeline.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# ── Stage enforcement ──────────────────────────────────────────────────────────
STAGES = [
    "INIT",
    "SOURCES_LOADED",
    "CONTENT_FETCHED",
    "CONTENT_EXTRACTED",
    "CONTENT_NORMALISED",
    "ENTITIES_EXTRACTED",
    "ENTITIES_RESOLVED",
    "ENTITY_SENTIMENT_SCORED",
    "QA_AND_CONFLICTS_CHECKED",
    "REPORTS_GENERATED",
    "COST_REPORT_GENERATED",
    "RESULTS_FINALISED",
]

_current_stage = "INIT"


def advance(expected: str, next_stage: str) -> None:
    global _current_stage
    assert _current_stage == expected, (
        f"[STAGE VIOLATION] Expected stage '{expected}', "
        f"but current stage is '{_current_stage}'"
    )
    _current_stage = next_stage
    logger.info(f"[STAGE] → {_current_stage}")


# ── Stage imports ──────────────────────────────────────────────────────────────
from Stages.SOURCES_LOADED import load_sources
from Stages.CONTENT_FETCHED import fetch_content
from Stages.CONTENT_EXTRACTED import extract_content
from Stages.CONTENT_NORMALISED import normalise_content
from Stages.ENTITIES_EXTRACTED import extract_entities
from Stages.ENTITIES_RESOLVED import resolve_entities
from Stages.ENTITY_SENTIMENT_SCORED import score_entity_sentiment
from Stages.QA_AND_CONFLICTS_CHECKED import run_qa_and_conflicts
from Stages.REPORTS_GENERATED import generate_reports
from Stages.COST_REPORT_GENERATED import generate_cost_report
from Stages.RESULTS_FINALISED import finalise
from utils.config import get_budget_limit
from utils.llm_client import configure_budget, BudgetExceededError


def main() -> None:
    pipeline_start = time.time()
    pipeline_errors: list[str] = []

    # Initialise budget guard — raises BudgetExceededError mid-run if limit hit
    budget = get_budget_limit()
    if budget is not None:
        configure_budget(budget)
        logger.info(f"[PIPELINE] Budget limit: ${budget:.2f} USD")
    else:
        logger.info("[PIPELINE] No budget limit configured (pipeline.budget_limit_usd is null)")

    # ── Stage 1: Load sources ──────────────────────────────────────────────────
    advance("INIT", "SOURCES_LOADED")
    sources = load_sources("sources.json")

    # ── Stage 2: Fetch content ─────────────────────────────────────────────────
    advance("SOURCES_LOADED", "CONTENT_FETCHED")
    html_map, fetch_errors = fetch_content(sources)
    # Do NOT extend pipeline_errors here — finalise() already merges fetch_errors
    # with pipeline_errors and dedupes, so duplicate inclusion here used to double-log.

    # ── Stage 3: Extract content ───────────────────────────────────────────────
    advance("CONTENT_FETCHED", "CONTENT_EXTRACTED")
    raw_items = extract_content(html_map)
    raw_count = len(raw_items)

    # ── Stage 4: Normalise content ─────────────────────────────────────────────
    advance("CONTENT_EXTRACTED", "CONTENT_NORMALISED")
    content_items = normalise_content(raw_items)

    if not content_items:
        logger.warning("[PIPELINE] No content items after normalisation. Proceeding with empty set.")

    # ── Stage 5: Extract entity mentions ──────────────────────────────────────
    advance("CONTENT_NORMALISED", "ENTITIES_EXTRACTED")
    raw_mentions = extract_entities(content_items)

    # ── Stage 6: Resolve entities ──────────────────────────────────────────────
    advance("ENTITIES_EXTRACTED", "ENTITIES_RESOLVED")
    entities = resolve_entities(raw_mentions)

    # ── Stage 7: Score entity sentiment ───────────────────────────────────────
    advance("ENTITIES_RESOLVED", "ENTITY_SENTIMENT_SCORED")
    sentiments = score_entity_sentiment(entities, content_items)

    # ── Stage 8: QA and conflict detection ────────────────────────────────────
    advance("ENTITY_SENTIMENT_SCORED", "QA_AND_CONFLICTS_CHECKED")
    qa_issues = run_qa_and_conflicts(entities, sentiments, content_items)

    # ── Stage 9: Generate reports ──────────────────────────────────────────────
    advance("QA_AND_CONFLICTS_CHECKED", "REPORTS_GENERATED")
    written_reports = generate_reports(entities, sentiments, qa_issues, content_items)
    logger.info(f"[PIPELINE] Reports written: {written_reports}")

    # ── Stage 10: Cost report ──────────────────────────────────────────────────
    advance("REPORTS_GENERATED", "COST_REPORT_GENERATED")
    cost_report = generate_cost_report()

    # ── Stage 11: Finalise ─────────────────────────────────────────────────────
    advance("COST_REPORT_GENERATED", "RESULTS_FINALISED")
    finalise(
        pipeline_start=pipeline_start,
        sources=sources,
        fetch_errors=fetch_errors,
        raw_items_count=raw_count,
        content_items=content_items,
        entities=entities,
        sentiments=sentiments,
        qa_issues=qa_issues,
        pipeline_errors=pipeline_errors,
    )

    duration = time.time() - pipeline_start
    logger.info(
        f"[PIPELINE COMPLETE] {len(entities)} entities, "
        f"{len(sentiments)} sentiment records, "
        f"{len(qa_issues)} QA issues, "
        f"${cost_report.get('total_cost_usd', 0):.4f} USD, "
        f"{duration:.1f}s"
    )
    if budget is not None:
        spent = cost_report.get("total_cost_usd", 0)
        pct = spent / budget * 100
        logger.info(f"[BUDGET] ${spent:.4f} / ${budget:.2f} used ({pct:.0f}%)")
    print("\n[PIPELINE COMPLETE]")


if __name__ == "__main__":
    main()
