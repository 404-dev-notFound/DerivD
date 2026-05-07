"""
config.py — single source of truth for pipeline configuration.

Loads config.json at import time (cached).
Environment variables override config.json values for secrets and deploy-time overrides.
Never raises — missing config falls back to sane defaults so the pipeline always boots.
"""
from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from typing import Any

logger = logging.getLogger(__name__)

_CONFIG_FILENAME = "config.json"
_DEFAULT_MODEL = "anthropic/claude-sonnet-4.5"
_DEFAULT_MAX_TOKENS = 4096
_DEFAULT_LOW_CONF_THRESHOLD = 0.6
_DEFAULT_SPAN_OVERLAP = 0.5


def _config_path() -> str:
    """Resolve config.json relative to project root (one level above this file)."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, _CONFIG_FILENAME)


@lru_cache(maxsize=1)
def load_config() -> dict[str, Any]:
    """Load config.json once. Returns empty dict if the file is missing or malformed."""
    path = _config_path()
    if not os.path.exists(path):
        logger.warning(f"[CONFIG] {path} not found; falling back to env vars and defaults")
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
        logger.info(f"[CONFIG] Loaded {path}")
        return cfg
    except (OSError, json.JSONDecodeError) as exc:
        logger.error(f"[CONFIG] Failed to parse {path}: {exc}")
        return {}


def get_model_for_stage(stage: str) -> str:
    """
    Resolve the LLM model for a given stage name.

    Resolution order:
      1. LLM_MODEL_<STAGE> env var (e.g. LLM_MODEL_ENTITY_EXTRACTION)
      2. config.json stages[stage].model_tier → models[tier]
      3. LLM_MODEL env var
      4. Hard default (Sonnet 4.5)
    """
    env_override = os.environ.get(f"LLM_MODEL_{stage.upper()}")
    if env_override:
        return env_override

    cfg = load_config()
    stages = cfg.get("stages", {})
    stage_cfg = stages.get(stage, {})
    tier = stage_cfg.get("model_tier") or cfg.get("defaults", {}).get("model_tier")

    models = cfg.get("models", {})
    if tier and tier in models:
        return models[tier]

    return os.environ.get("LLM_MODEL") or _DEFAULT_MODEL


def get_max_tokens_for_stage(stage: str) -> int:
    """Per-stage max_tokens, falling back to env MAX_TOKENS then default."""
    cfg = load_config()
    stage_cfg = cfg.get("stages", {}).get(stage, {})
    value = stage_cfg.get("max_tokens") or cfg.get("defaults", {}).get("max_tokens")
    if value:
        return int(value)
    env_value = os.environ.get("MAX_TOKENS")
    if env_value and env_value.isdigit():
        return int(env_value)
    return _DEFAULT_MAX_TOKENS


def get_stage_param(stage: str, key: str, default: Any = None) -> Any:
    """Read any per-stage config key (e.g. batch_size, chunk_size)."""
    cfg = load_config()
    stage_cfg = cfg.get("stages", {}).get(stage, {})
    return stage_cfg.get(key, default)


def get_default(key: str, fallback: Any = None) -> Any:
    """Read a value from the defaults block."""
    cfg = load_config()
    return cfg.get("defaults", {}).get(key, fallback)


def get_pricing(model: str) -> dict[str, float]:
    """
    Return {"input": $/M, "output": $/M} for a model.
    Falls back to Sonnet pricing if the model isn't registered.
    """
    cfg = load_config()
    pricing = cfg.get("pricing_usd_per_million", {})
    if model in pricing:
        return {
            "input": float(pricing[model].get("input", 3.00)),
            "output": float(pricing[model].get("output", 15.00)),
        }
    return {"input": 3.00, "output": 15.00}


def get_efficiency_flag(flag: str, default: bool = False) -> bool:
    """Read a boolean flag from the efficiency block."""
    cfg = load_config()
    return bool(cfg.get("efficiency", {}).get(flag, default))


def get_artifact_mirror_dir() -> str | None:
    """
    Return the mirror directory (e.g. 'Artifacts') if mirroring is enabled,
    otherwise None. Pipeline stages that write JSON outputs will also copy
    to this directory so the evaluator can find artifacts in one place.
    """
    if not get_efficiency_flag("enable_artifact_mirror", False):
        return None
    cfg = load_config()
    return cfg.get("efficiency", {}).get("artifact_mirror_dir") or None


def get_low_confidence_threshold() -> float:
    """Threshold below which entities are flagged in qa_report.json."""
    return float(get_default("low_confidence_threshold", _DEFAULT_LOW_CONF_THRESHOLD))


def get_span_overlap_threshold() -> float:
    """Word-overlap threshold for hallucination span validation."""
    return float(get_default("span_overlap_threshold", _DEFAULT_SPAN_OVERLAP))


def get_budget_limit() -> float | None:
    """
    Per-run spend cap in USD from config.pipeline.budget_limit_usd.
    Returns None if unset or null, meaning no limit is enforced.
    """
    cfg = load_config()
    val = cfg.get("pipeline", {}).get("budget_limit_usd")
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None
