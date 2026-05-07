#!/usr/bin/env python3
"""
run_pipeline.py — Single entry point for the financial content intelligence pipeline.

Stage order is enforced by advance(). Skipping or reordering stages raises AssertionError.
All stages are imported from Stages/ and called through this orchestrator.

Usage:
    python run_pipeline.py              # full run
    python run_pipeline.py --resume     # skip stages whose artifacts already exist on disk
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()

# ── Logging setup ──────────────────────────────────────────────────────────────
# Windows legacy consoles default to cp1252 which can't encode arrows / em-dashes
# used in log messages. Force stdout to UTF-8 so log output never crashes.
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
    logger.info(f"[STAGE] -> {_current_stage}")


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
from utils.artifact_store import read_json_artifact, artifact_is_valid
from utils.config import get_budget_limit
from utils.llm_client import configure_budget, BudgetExceededError
from utils.paths import (
    SOURCES,
    EXTRACTED_CONTENT,
    ENTITIES,
    ENTITY_SENTIMENT,
    QA_REPORT,
    REPORTS_DIR,
)


def _log_resume_skip(stage: str, artifact: str) -> None:
    logger.info(f"[RESUME] Skipping {stage} — artifact already exists: {artifact}")


def main(resume: bool = False) -> None:
    pipeline_start = time.time()
    pipeline_errors: list[str] = []

    if resume:
        logger.info("[PIPELINE] Resume mode: will reuse valid existing artifacts")

    # Initialise budget guard — raises BudgetExceededError mid-run if limit hit
    budget = get_budget_limit()
    if budget is not None:
        configure_budget(budget)
        logger.info(f"[PIPELINE] Budget limit: ${budget:.2f} USD")
    else:
        logger.info("[PIPELINE] No budget limit configured (pipeline.budget_limit_usd is null)")

    # ── Stage 1: Load sources ──────────────────────────────────────────────────
    advance("INIT", "SOURCES_LOADED")
    sources = load_sources(SOURCES)

    # ── Stage 2-4: Fetch → Extract → Normalise ────────────────────────────────
    # These three stages are skipped together in resume mode because the
    # intermediates (raw HTML, raw items) are not persisted — only the final
    # extracted_content.json is on disk.
    if resume and artifact_is_valid(EXTRACTED_CONTENT):
        _log_resume_skip("CONTENT_FETCHED + CONTENT_EXTRACTED + CONTENT_NORMALISED", EXTRACTED_CONTENT)
        advance("SOURCES_LOADED", "CONTENT_FETCHED")
        advance("CONTENT_FETCHED", "CONTENT_EXTRACTED")
        advance("CONTENT_EXTRACTED", "CONTENT_NORMALISED")
        content_items = read_json_artifact(EXTRACTED_CONTENT)
        fetch_errors: list[str] = []
        raw_count = len(content_items)
    else:
        advance("SOURCES_LOADED", "CONTENT_FETCHED")
        html_map, fetch_errors = fetch_content(sources)

        advance("CONTENT_FETCHED", "CONTENT_EXTRACTED")
        raw_items = extract_content(html_map)
        raw_count = len(raw_items)

        advance("CONTENT_EXTRACTED", "CONTENT_NORMALISED")
        content_items = normalise_content(raw_items)

    if not content_items:
        logger.warning("[PIPELINE] No content items. Proceeding with empty set.")

    # ── Stage 5-6: Extract + Resolve entities ─────────────────────────────────
    if resume and artifact_is_valid(ENTITIES):
        _log_resume_skip("ENTITIES_EXTRACTED + ENTITIES_RESOLVED", ENTITIES)
        advance("CONTENT_NORMALISED", "ENTITIES_EXTRACTED")
        advance("ENTITIES_EXTRACTED", "ENTITIES_RESOLVED")
        entities = read_json_artifact(ENTITIES)
        raw_mentions: list[dict] = []  # not needed downstream
    else:
        advance("CONTENT_NORMALISED", "ENTITIES_EXTRACTED")
        raw_mentions = extract_entities(content_items)

        advance("ENTITIES_EXTRACTED", "ENTITIES_RESOLVED")
        entities = resolve_entities(raw_mentions)

    # ── Stage 7: Sentiment scoring ─────────────────────────────────────────────
    if resume and artifact_is_valid(ENTITY_SENTIMENT):
        _log_resume_skip("ENTITY_SENTIMENT_SCORED", ENTITY_SENTIMENT)
        advance("ENTITIES_RESOLVED", "ENTITY_SENTIMENT_SCORED")
        sentiments = read_json_artifact(ENTITY_SENTIMENT)
    else:
        advance("ENTITIES_RESOLVED", "ENTITY_SENTIMENT_SCORED")
        sentiments = score_entity_sentiment(entities, content_items)

    # ── Stage 8: QA and conflict detection ────────────────────────────────────
    if resume and artifact_is_valid(QA_REPORT):
        _log_resume_skip("QA_AND_CONFLICTS_CHECKED", QA_REPORT)
        advance("ENTITY_SENTIMENT_SCORED", "QA_AND_CONFLICTS_CHECKED")
        qa_issues = read_json_artifact(QA_REPORT)
    else:
        advance("ENTITY_SENTIMENT_SCORED", "QA_AND_CONFLICTS_CHECKED")
        qa_issues = run_qa_and_conflicts(entities, sentiments, content_items)

    # ── Stage 9: Generate reports ──────────────────────────────────────────────
    if resume and os.path.isdir(REPORTS_DIR) and os.listdir(REPORTS_DIR):
        _log_resume_skip("REPORTS_GENERATED", REPORTS_DIR)
        advance("QA_AND_CONFLICTS_CHECKED", "REPORTS_GENERATED")
        written_reports = [
            os.path.join(REPORTS_DIR, f) for f in os.listdir(REPORTS_DIR)
        ]
    else:
        advance("QA_AND_CONFLICTS_CHECKED", "REPORTS_GENERATED")
        written_reports = generate_reports(entities, sentiments, qa_issues, content_items)
    logger.info(f"[PIPELINE] Reports: {[os.path.basename(r) for r in written_reports]}")

    # ── Stage 10: Cost report (always regenerate — reads llm_calls.jsonl) ─────
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
    parser = argparse.ArgumentParser(description="Financial Content Intelligence Pipeline")
    parser.add_argument(
        "--resume",
        action="store_true",
        help=(
            "Skip stages whose output artifacts already exist in Artifacts/. "
            "Useful when a run was interrupted after expensive LLM stages completed."
        ),
    )
    args = parser.parse_args()
    main(resume=args.resume)
