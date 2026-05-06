"""
Stage — COST_REPORT_GENERATED: Read llm_calls.jsonl, compute costs → cost_report.json.
Fully deterministic — no LLM.
"""
from __future__ import annotations
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger(__name__)
OUTPUT_PATH = "cost_report.json"
LLM_LOG_PATH = "llm_calls.jsonl"

PRICING = {
    "anthropic/claude-sonnet-4-5": {"input_per_million": 3.00, "output_per_million": 15.00},
    "claude-sonnet-4-5":           {"input_per_million": 3.00, "output_per_million": 15.00},
    "anthropic/claude-haiku-4-5":  {"input_per_million": 0.25, "output_per_million": 1.25},
    "claude-haiku-4-5":            {"input_per_million": 0.25, "output_per_million": 1.25},
}
DEFAULT_PRICING = {"input_per_million": 3.00, "output_per_million": 15.00}


def compute_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    p = PRICING.get(model, DEFAULT_PRICING)
    return (
        input_tokens / 1_000_000 * p["input_per_million"]
        + output_tokens / 1_000_000 * p["output_per_million"]
    )


def generate_cost_report() -> dict:
    """
    Read all LLM call logs, compute per-stage and total costs.
    Returns the report dict and writes cost_report.json.
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

    total_input = sum(c.get("estimated_input_tokens", 0) for c in calls)
    total_output = sum(c.get("estimated_output_tokens", 0) for c in calls)
    failed_calls = sum(1 for c in calls if c.get("error"))

    by_stage: dict[str, dict] = {}
    for c in calls:
        stage = c.get("stage", "unknown")
        model = c.get("model", "claude-sonnet-4-5")
        inp = c.get("estimated_input_tokens", 0)
        out = c.get("estimated_output_tokens", 0)
        if stage not in by_stage:
            by_stage[stage] = {
                "call_count": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0.0,
            }
        by_stage[stage]["call_count"] += 1
        by_stage[stage]["input_tokens"] += inp
        by_stage[stage]["output_tokens"] += out
        by_stage[stage]["cost_usd"] += compute_cost(model, inp, out)

    report = {
        "total_llm_calls": len(calls),
        "failed_calls": failed_calls,
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_cost_usd": round(
            sum(s["cost_usd"] for s in by_stage.values()), 6
        ),
        "by_stage": by_stage,
        "efficiency_strategy": (
            "Content deduplication via SHA-256 hash before LLM calls eliminates "
            "redundant processing of repeated content across sources. "
            "Entity mentions batched per source (batch_size=35) to reduce LLM round-trips. "
            "Fallback to empty responses on LLM failure prevents pipeline stalls."
        ),
    }

    _write(report)
    logger.info(
        f"[COST] Total: {len(calls)} calls, "
        f"{total_input} input / {total_output} output tokens, "
        f"${report['total_cost_usd']:.4f} USD"
    )
    return report


def _empty_report() -> dict:
    return {
        "total_llm_calls": 0,
        "failed_calls": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_cost_usd": 0.0,
        "by_stage": {},
        "efficiency_strategy": "No LLM calls recorded.",
    }


def _write(report: dict) -> None:
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    logger.info(f"[COST] Written → {OUTPUT_PATH}")
