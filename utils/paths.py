"""
paths.py — single source of truth for all artifact file paths.

All generated artifacts live under Artifacts/ (configured in config.json
or defaulting here). Import from this module rather than hardcoding strings.
"""
from __future__ import annotations

import os

# Base directory for all generated artifacts.
# Resolved relative to project root (one level above this file).
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARTIFACTS_DIR = os.path.join(_ROOT, "Artifacts")

os.makedirs(ARTIFACTS_DIR, exist_ok=True)


def _a(filename: str) -> str:
    """Return absolute path to a file inside Artifacts/."""
    return os.path.join(ARTIFACTS_DIR, filename)


# ── Generated JSON artifacts ────────────────────────────────────────────────
EXTRACTED_CONTENT = _a("extracted_content.json")
ENTITIES          = _a("entities.json")
ENTITY_SENTIMENT  = _a("entity_sentiment.json")
QA_REPORT         = _a("qa_report.json")
COST_REPORT       = _a("cost_report.json")
RUN_METRICS       = _a("run_metrics.json")
SENTIMENT_TIMELINE = _a("sentiment_timeline.json")
LLM_CALLS         = _a("llm_calls.jsonl")

# Reports subdirectory
REPORTS_DIR = _a("reports")

# Input file (stays in project root — evaluator may replace it)
SOURCES = os.path.join(_ROOT, "sources.json")
