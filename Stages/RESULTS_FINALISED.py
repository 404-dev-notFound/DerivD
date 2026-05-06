"""
Stage — RESULTS_FINALISED: Write run_metrics.json + sentiment_timeline.json.
Also mirror every produced JSON artifact into the Artifacts/ directory
(if config.efficiency.enable_artifact_mirror is true), so evaluators and
the REST API can serve artifacts from a single well-known folder.

Fully deterministic — no LLM.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.config import get_artifact_mirror_dir, get_low_confidence_threshold
from utils.models import RunMetrics, TimelineItem

logger = logging.getLogger(__name__)
METRICS_PATH = "run_metrics.json"
TIMELINE_PATH = "sentiment_timeline.json"

# JSON artifacts that should appear inside Artifacts/ for evaluator convenience.
# sources.json is listed because the evaluator can drop a replacement there.
_MIRROR_FILES = [
    "sources.json",
    "extracted_content.json",
    "entities.json",
    "entity_sentiment.json",
    "qa_report.json",
    "cost_report.json",
    "run_metrics.json",
    "sentiment_timeline.json",
    "llm_calls.jsonl",
]


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
    """Write run_metrics.json, sentiment_timeline.json, then mirror artifacts."""
    duration = time.time() - pipeline_start

    llm_call_count = 0
    if os.path.exists("llm_calls.jsonl"):
        with open("llm_calls.jsonl", encoding="utf-8") as f:
            llm_call_count = sum(1 for line in f if line.strip())

    threshold = get_low_confidence_threshold()
    low_conf = sum(
        1 for e in entities
        if e.get("resolution_confidence", 1.0) < threshold
    )

    # Dedupe error log while preserving order — fetch_errors and pipeline_errors
    # can overlap because fetch_errors is forwarded into pipeline_errors upstream.
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

    with open(METRICS_PATH, "w", encoding="utf-8") as f:
        json.dump(metrics.model_dump(), f, ensure_ascii=False, indent=2)
    logger.info(f"[FINALISED] Written → {METRICS_PATH}")

    timeline = _build_timeline(sentiments, content_items)
    with open(TIMELINE_PATH, "w", encoding="utf-8") as f:
        json.dump(timeline, f, ensure_ascii=False, indent=2)
    logger.info(
        f"[FINALISED] Written → {TIMELINE_PATH} ({len(timeline)} timeline items)"
    )

    _mirror_artifacts()


def _mirror_artifacts() -> None:
    """
    Copy every produced JSON artifact to the configured mirror directory
    (typically Artifacts/). Preserves original files in repo root for the
    validator which expects root-level paths.
    """
    mirror_dir = get_artifact_mirror_dir()
    if not mirror_dir:
        return

    os.makedirs(mirror_dir, exist_ok=True)
    copied = 0
    for filename in _MIRROR_FILES:
        if os.path.exists(filename):
            try:
                shutil.copy2(filename, os.path.join(mirror_dir, filename))
                copied += 1
            except OSError as exc:
                logger.warning(f"[MIRROR] Failed to copy {filename}: {exc}")

    # Mirror reports directory too (markdown files)
    if os.path.isdir("reports"):
        reports_mirror = os.path.join(mirror_dir, "reports")
        os.makedirs(reports_mirror, exist_ok=True)
        for report_name in os.listdir("reports"):
            src = os.path.join("reports", report_name)
            if os.path.isfile(src):
                try:
                    shutil.copy2(src, os.path.join(reports_mirror, report_name))
                    copied += 1
                except OSError as exc:
                    logger.warning(f"[MIRROR] Failed to copy report {report_name}: {exc}")

    logger.info(f"[MIRROR] Mirrored {copied} artifact(s) → {mirror_dir}/")


def _build_timeline(
    sentiments: list[dict], content_items: list[dict]
) -> list[dict]:
    """
    Build sentiment timeline from dated content items.
    Only includes items where published_at is available.
    Falls back to extracted_at if no published_at is set (pipeline runs always have this).
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
