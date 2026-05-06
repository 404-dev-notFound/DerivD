"""
Stage — RESULTS_FINALISED: Write run_metrics.json + sentiment_timeline.json.
All artifacts already live in Artifacts/ (written directly by each stage via utils/paths.py).
No mirroring needed. Fully deterministic — no LLM.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.config import get_low_confidence_threshold
from utils.models import RunMetrics, TimelineItem
from utils.paths import RUN_METRICS, SENTIMENT_TIMELINE, LLM_CALLS

logger = logging.getLogger(__name__)


def finalise(
    pipeline_start: float,
    sources: list[str],
    fetch_errors: list[str],
    raw_items_count: int,
    content_items: list[dict],
    entities: list[dict],
    sentiments: list[dict],
    qa_issues: list[dict],
    pipeline_errors: list[str],
) -> None:
    """Write run_metrics.json and sentiment_timeline.json into Artifacts/."""
    duration = time.time() - pipeline_start

    llm_call_count = 0
    if os.path.exists(LLM_CALLS):
        with open(LLM_CALLS, encoding="utf-8") as f:
            llm_call_count = sum(1 for line in f if line.strip())

    threshold = get_low_confidence_threshold()
    low_conf = sum(
        1 for e in entities
        if e.get("resolution_confidence", 1.0) < threshold
    )

    # Dedupe error log — fetch_errors may overlap with pipeline_errors
    combined: list[str] = []
    seen: set[str] = set()
    for err in list(fetch_errors) + list(pipeline_errors):
        if err and err not in seen:
            combined.append(err)
            seen.add(err)

    metrics = RunMetrics(
        total_sources=len(sources),
        sources_fetched_ok=len(sources) - len(fetch_errors),
        sources_failed=len(fetch_errors),
        total_content_items=raw_items_count,
        content_items_after_dedup=len(content_items),
        total_entities=len(entities),
        low_confidence_entities=low_conf,
        sentiment_records=len(sentiments),
        qa_issues=len(qa_issues),
        llm_call_count=llm_call_count,
        pipeline_duration_seconds=round(duration, 2),
        error_log=combined,
    )

    with open(RUN_METRICS, "w", encoding="utf-8") as f:
        json.dump(metrics.model_dump(), f, ensure_ascii=False, indent=2)
    logger.info(f"[FINALISED] Written -> {RUN_METRICS}")

    timeline = _build_timeline(sentiments, content_items)
    with open(SENTIMENT_TIMELINE, "w", encoding="utf-8") as f:
        json.dump(timeline, f, ensure_ascii=False, indent=2)
    logger.info(
        f"[FINALISED] Written -> {SENTIMENT_TIMELINE} ({len(timeline)} timeline items)"
    )


def _build_timeline(
    sentiments: list[dict], content_items: list[dict]
) -> list[dict]:
    """
    Build sentiment timeline from dated content items.
    Falls back to extracted_at when published_at is not set.
    Deterministic — no LLM.
    """
    dated_content = {
        c["content_id"]: c
        for c in content_items
        if c.get("published_at")
    }

    if not dated_content:
        dated_content = {
            c["content_id"]: c
            for c in content_items
            if c.get("extracted_at")
        }
        ts_field = "extracted_at"
    else:
        ts_field = "published_at"

    timeline: list[dict] = []
    for sent in sentiments:
        entity_id = sent["entity_id"]
        dated_evidence = [
            ev for ev in sent.get("evidence", [])
            if ev.get("content_id") in dated_content
        ]
        if not dated_evidence:
            continue

        groups: dict[str, list[str]] = {}
        for ev in dated_evidence:
            ts = dated_content[ev["content_id"]].get(ts_field, "")
            date_key = ts[:10] if ts else "unknown"
            groups.setdefault(date_key, []).append(ev["content_id"])

        for timestamp, cids in sorted(groups.items()):
            try:
                item = TimelineItem(
                    entity_id=entity_id,
                    timestamp=timestamp,
                    sentiment=sent.get("sentiment", "neutral"),
                    source_content_ids=cids,
                    summary=(
                        f"{sent.get('canonical_name', entity_id)} sentiment: "
                        f"{sent.get('sentiment')} (score={sent.get('sentiment_score', 0):.2f})"
                    ),
                )
                timeline.append(item.model_dump())
            except Exception as e:
                logger.warning(f"[FINALISED] Invalid timeline item: {e}")

    return timeline
