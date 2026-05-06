"""
Stage — RESULTS_FINALISED: Write run_metrics.json + sentiment_timeline.json.
Fully deterministic — no LLM.
"""
from __future__ import annotations
import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.models import RunMetrics, TimelineItem

logger = logging.getLogger(__name__)
METRICS_PATH = "run_metrics.json"
TIMELINE_PATH = "sentiment_timeline.json"


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
    """Write run_metrics.json and sentiment_timeline.json."""
    duration = time.time() - pipeline_start

    # Count LLM calls
    llm_call_count = 0
    if os.path.exists("llm_calls.jsonl"):
        with open("llm_calls.jsonl", encoding="utf-8") as f:
            llm_call_count = sum(1 for line in f if line.strip())

    low_conf = sum(
        1 for e in entities
        if e.get("resolution_confidence", 1.0) < 0.6
    )

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
        error_log=fetch_errors + pipeline_errors,
    )

    with open(METRICS_PATH, "w", encoding="utf-8") as f:
        json.dump(metrics.model_dump(), f, ensure_ascii=False, indent=2)
    logger.info(f"[FINALISED] Written → {METRICS_PATH}")

    # Build sentiment timeline from dated content
    timeline = _build_timeline(sentiments, content_items)
    with open(TIMELINE_PATH, "w", encoding="utf-8") as f:
        json.dump(timeline, f, ensure_ascii=False, indent=2)
    logger.info(
        f"[FINALISED] Written → {TIMELINE_PATH} ({len(timeline)} timeline items)"
    )


def _build_timeline(
    sentiments: list[dict], content_items: list[dict]
) -> list[dict]:
    """
    Build sentiment timeline from dated content items.
    Only includes items where published_at is available.
    Deterministic — no LLM.
    """
    dated_content = {
        c["content_id"]: c
        for c in content_items
        if c.get("published_at")
    }

    if not dated_content:
        # Fall back to extracted_at timestamps
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

        # Group by timestamp (date part)
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
