"""
Stage — COST_REPORT_GENERATED: Read llm_calls.jsonl, compute costs → cost_report.json.
Fully deterministic — no LLM.

Breakdowns produced (required by problem.md):
- total cost
- cost per stage
- cost per source URL (distributed proportionally from content → llm calls)
- cost per entity
- model routing summary (which model each stage used — validates tiered-routing savings)
"""
from __future__ import annotations

import json
import logging
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.config import get_pricing, load_config

logger = logging.getLogger(__name__)
OUTPUT_PATH = "cost_report.json"
LLM_LOG_PATH = "llm_calls.jsonl"


def compute_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Compute USD cost for a single call from config-driven pricing table."""
    p = get_pricing(model)
    return (
        input_tokens / 1_000_000 * p["input"]
        + output_tokens / 1_000_000 * p["output"]
    )


def _load_content_index() -> dict[str, str]:
    """Map content_id → source_url from extracted_content.json, if available."""
    if not os.path.exists("extracted_content.json"):
        return {}
    try:
        with open("extracted_content.json", encoding="utf-8") as f:
            content = json.load(f)
        return {c["content_id"]: c.get("source_url", "unknown") for c in content}
    except (OSError, json.JSONDecodeError, KeyError):
        return {}


def _load_entities_count() -> int:
    """Number of resolved entities (for per-entity cost average)."""
    if not os.path.exists("entities.json"):
        return 0
    try:
        with open("entities.json", encoding="utf-8") as f:
            entities = json.load(f)
        return len(entities) if isinstance(entities, list) else 0
    except (OSError, json.JSONDecodeError):
        return 0


def generate_cost_report() -> dict:
    """
    Read all LLM call logs, compute costs with per-stage, per-source, per-entity,
    and per-model breakdowns. Returns the report dict and writes cost_report.json.
    """
    if not os.path.exists(LLM_LOG_PATH):
        logger.warning(f"[COST] {LLM_LOG_PATH} not found; writing empty cost report")
        report = _empty_report()
        _write(report)
        return report

    calls: list[dict] = []
    with open(LLM_LOG_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                calls.append(json.loads(line))
            except json.JSONDecodeError:
                logger.warning(f"[COST] Skipped malformed log line: {line[:80]}")

    content_to_source = _load_content_index()
    total_entities = _load_entities_count()

    by_stage: dict[str, dict] = defaultdict(
        lambda: {"call_count": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "model": "unknown"}
    )
    by_model: dict[str, dict] = defaultdict(
        lambda: {"call_count": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    )
    by_source: dict[str, float] = defaultdict(float)

    total_input = 0
    total_output = 0
    failed_calls = 0

    for c in calls:
        stage = c.get("stage", "unknown")
        model = c.get("model", "unknown")
        inp = int(c.get("estimated_input_tokens", 0) or 0)
        out = int(c.get("estimated_output_tokens", 0) or 0)
        cost = compute_cost(model, inp, out)

        total_input += inp
        total_output += out
        if c.get("error"):
            failed_calls += 1

        by_stage[stage]["call_count"] += 1
        by_stage[stage]["input_tokens"] += inp
        by_stage[stage]["output_tokens"] += out
        by_stage[stage]["cost_usd"] = round(by_stage[stage]["cost_usd"] + cost, 6)
        by_stage[stage]["model"] = model

        by_model[model]["call_count"] += 1
        by_model[model]["input_tokens"] += inp
        by_model[model]["output_tokens"] += out
        by_model[model]["cost_usd"] = round(by_model[model]["cost_usd"] + cost, 6)

        # Distribute cost across the source URLs that contributed content to this call
        cids = [cid for cid in c.get("content_ids", []) if cid in content_to_source]
        if cids:
            sources_in_call = {content_to_source[cid] for cid in cids}
            per_source = cost / len(sources_in_call)
            for url in sources_in_call:
                by_source[url] = round(by_source[url] + per_source, 6)
        else:
            # No content_ids on the call — attribute to "shared" bucket
            by_source["shared/aggregate"] = round(
                by_source["shared/aggregate"] + cost, 6
            )

    total_cost = round(sum(s["cost_usd"] for s in by_stage.values()), 6)
    cost_per_entity = (
        round(total_cost / total_entities, 6) if total_entities > 0 else 0.0
    )

    # Model routing summary — validates tiered-routing efficiency
    cfg = load_config()
    model_tiers = cfg.get("models", {})
    routing_summary = {
        stage: {
            "model_used": info["model"],
            "calls": info["call_count"],
            "cost_usd": info["cost_usd"],
            "tier": _tier_for_model(info["model"], model_tiers),
        }
        for stage, info in by_stage.items()
    }

    report = {
        "total_llm_calls": len(calls),
        "failed_calls": failed_calls,
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_cost_usd": total_cost,
        "cost_per_entity_usd": cost_per_entity,
        "entities_resolved": total_entities,
        "by_stage": dict(by_stage),
        "by_model": dict(by_model),
        "by_source": dict(by_source),
        "model_routing": routing_summary,
        "efficiency_strategy": (
            "Three strategies applied: "
            "(1) Tiered model routing — classification-heavy stages (entity_extraction, "
            "qa_and_conflict_detection) use Haiku 4.5 while reasoning-heavy stages "
            "(entity_resolution, entity_sentiment_scoring, report_generation) use Sonnet 4.5. "
            "(2) SHA-256 content hashing in CONTENT_NORMALISED eliminates duplicate content "
            "before any LLM sees it. "
            "(3) Batching of entity mentions per source (batch_size configurable in config.json) "
            "reduces LLM round-trips. "
            "Per-stage model selection is defined in config.json under stages.<stage>.model_tier."
        ),
    }

    _write(report)
    logger.info(
        f"[COST] Total: {len(calls)} calls, "
        f"{total_input} input / {total_output} output tokens, "
        f"${total_cost:.4f} USD "
        f"(cost/entity=${cost_per_entity:.4f})"
    )
    return report


def _tier_for_model(model: str, tiers: dict[str, str]) -> str:
    """Reverse-lookup: which tier name maps to this model."""
    for tier, tier_model in tiers.items():
        if tier_model == model:
            return tier
    return "custom/untiered"


def _empty_report() -> dict:
    return {
        "total_llm_calls": 0,
        "failed_calls": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_cost_usd": 0.0,
        "cost_per_entity_usd": 0.0,
        "entities_resolved": 0,
        "by_stage": {},
        "by_model": {},
        "by_source": {},
        "model_routing": {},
        "efficiency_strategy": "No LLM calls recorded.",
    }


def _write(report: dict) -> None:
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    logger.info(f"[COST] Written → {OUTPUT_PATH}")
