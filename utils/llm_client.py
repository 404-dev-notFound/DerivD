"""
OpenRouter LLM client.

Cost optimisations applied (cost-aware-llm-pipeline skill):
  1. Prompt caching   — hallucination rules and stage system prompt sent with
                        cache_control:ephemeral so they are only charged once
                        per 5-minute TTL window instead of on every call.
  2. Narrow retry     — only retries on transient failures (429, 5xx, network).
                        Auth and bad-request errors fail immediately instead of
                        burning retries and erroneously spending budget.
  3. Budget guard     — module-level spend counter checked before every call.
                        Set via config.json pipeline.budget_limit_usd.
                        BudgetExceededError is raised before the HTTP call is made.

Deterministic responsibilities:
- Loading anti-hallucination rules from hallucination.md (single source of truth)
- Making HTTP calls to OpenRouter with per-call max_tokens override
- Logging every call to llm_calls.jsonl
- Recovering from truncated JSON responses
- Validating evidence spans against source corpus (hallucination guard)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import datetime
import threading
from dataclasses import dataclass
from typing import Optional

import httpx
from dotenv import load_dotenv

from utils.config import (
    get_max_tokens_for_stage,
    get_model_for_stage,
    get_pricing,
    get_span_overlap_threshold,
)
from utils.paths import LLM_CALLS

load_dotenv()

logger = logging.getLogger(__name__)

OPENROUTER_BASE = "https://openrouter.ai/api/v1/chat/completions"

# ── Retry policy ───────────────────────────────────────────────────────────────
# Only retry transient failures. Fail fast on auth/bad-request so we don't
# waste budget burning retries on permanent errors.
MAX_RETRIES = 2
_RETRYABLE_HTTP_CODES = {429, 500, 502, 503, 504}

# ── Concurrency ────────────────────────────────────────────────────────────────
_log_lock = threading.Lock()   # serialise llm_calls.jsonl appends
_budget_lock = threading.Lock()

# ── Budget guard ───────────────────────────────────────────────────────────────
_total_spent_usd: float = 0.0
_budget_limit_usd: float | None = None


class BudgetExceededError(RuntimeError):
    def __init__(self, spent: float, limit: float) -> None:
        super().__init__(
            f"Pipeline budget exceeded: ${spent:.4f} spent >= ${limit:.4f} limit. "
            "Increase pipeline.budget_limit_usd in config.json to continue."
        )
        self.spent = spent
        self.limit = limit


def configure_budget(limit_usd: float) -> None:
    """Call once at pipeline startup to set the run-level spend limit."""
    global _budget_limit_usd, _total_spent_usd
    with _budget_lock:
        _budget_limit_usd = limit_usd
        _total_spent_usd = 0.0
    logger.info(f"[BUDGET] Limit set to ${limit_usd:.4f} USD for this run")


def _check_and_record_spend(cost_usd: float) -> None:
    """Atomically add cost and raise if over limit. Call AFTER a successful call."""
    global _total_spent_usd
    with _budget_lock:
        _total_spent_usd += cost_usd
        if _budget_limit_usd is not None and _total_spent_usd > _budget_limit_usd:
            raise BudgetExceededError(_total_spent_usd, _budget_limit_usd)


# ── Immutable cost record (for external introspection / tests) ─────────────────
@dataclass(frozen=True, slots=True)
class CostRecord:
    stage: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    cost_usd: float


# ── Hallucination rules — loaded once, cached for the process lifetime ─────────
def _load_hallucination_rules() -> str:
    hall_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "hallucination.md",
    )
    if not os.path.exists(hall_path):
        logger.warning("[HALLUCINATION] hallucination.md not found; rules not injected")
        return ""
    try:
        with open(hall_path, encoding="utf-8") as f:
            content = f.read()
        match = re.search(
            r"## Prompt-Level Constraints.*?```(.*?)```",
            content,
            re.DOTALL,
        )
        if match:
            rules = match.group(1).strip()
            logger.info("[HALLUCINATION] Rules loaded from hallucination.md")
            return rules
    except Exception as e:
        logger.warning(f"[HALLUCINATION] Could not load hallucination.md: {e}")
    return ""


_HALLUCINATION_RULES: str = _load_hallucination_rules()


# ── Prompt-cached message builder ──────────────────────────────────────────────
def _build_messages(stage_system: str, user_content: str) -> list[dict]:
    """
    Build the messages array with cache_control on the expensive repeated parts.

    Cache hierarchy (Anthropic prompt caching, passed through OpenRouter):
      Block 1 (cache_control): hallucination rules — identical on every pipeline call,
                                stays warm for 5 min → billed once per TTL window.
      Block 2 (cache_control): stage system prompt — identical within a batch stage
                                (e.g. all entity_extraction calls share the same prompt).
      Block 3:                  user content — varies per call, never cached.

    OpenRouter forwards cache_control to Anthropic when the model is anthropic/*.
    For non-Anthropic models the field is silently ignored (no error).
    """
    system_blocks: list[dict] = []

    if _HALLUCINATION_RULES:
        system_blocks.append({
            "type": "text",
            "text": _HALLUCINATION_RULES,
            "cache_control": {"type": "ephemeral"},
        })

    if stage_system:
        system_blocks.append({
            "type": "text",
            "text": stage_system,
            "cache_control": {"type": "ephemeral"},
        })

    # If no blocks at all, fall back to plain string to avoid empty content error.
    system_content: str | list[dict] = system_blocks if system_blocks else ""

    return [
        {"role": "system", "content": system_content},
        {"role": "user",   "content": user_content},
    ]


# ── HTTP client ────────────────────────────────────────────────────────────────
def _get_client() -> httpx.Client:
    return httpx.Client(timeout=180.0)


def _is_retryable(exc: Exception) -> bool:
    """
    True only for transient failures that are worth retrying.
    Auth errors (401/403) and bad-request errors (400) are permanent — retrying
    would just burn budget, so we return False for those.
    """
    if isinstance(exc, (httpx.ConnectError, httpx.TimeoutException)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_HTTP_CODES
    return False


# ── LLM call ───────────────────────────────────────────────────────────────────
def llm_call(
    stage: str,
    system: str,
    user_content: str,
    input_artifacts: list[str],
    output_artifact: str,
    source_url: Optional[str] = None,
    content_ids: Optional[list[str]] = None,
    max_tokens: Optional[int] = None,
) -> str:
    """
    Make one LLM call via OpenRouter, return response text.

    Cost controls applied automatically:
    - Tiered model routing per stage (config.json stages.<stage>.model_tier)
    - Prompt caching on hallucination rules + stage system prompt
    - Narrow retry (transient only)
    - Budget check after each successful call
    """
    api_key = os.environ["OPENROUTER_API_KEY"]
    model = get_model_for_stage(stage)
    effective_max_tokens = max_tokens or get_max_tokens_for_stage(stage)

    prompt_hash = hashlib.sha256(
        ((_HALLUCINATION_RULES or "") + system + user_content).encode()
    ).hexdigest()[:16]

    messages = _build_messages(system, user_content)
    payload = {
        "model": model,
        "max_tokens": effective_max_tokens,
        "messages": messages,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/financial-intelligence-pipeline",
        # Enable Anthropic prompt caching via OpenRouter
        "anthropic-beta": "prompt-caching-2024-07-31",
    }

    result = ""
    input_tokens = 0
    output_tokens = 0
    cache_read_tokens = 0
    cache_creation_tokens = 0
    last_error: Optional[str] = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            with _get_client() as client:
                resp = client.post(OPENROUTER_BASE, headers=headers, json=payload)
                resp.raise_for_status()

            data = resp.json()
            result = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            input_tokens = usage.get("prompt_tokens", 0)
            output_tokens = usage.get("completion_tokens", 0)
            # Anthropic cache fields (present when caching is active)
            cache_read_tokens = usage.get("cache_read_input_tokens", 0)
            cache_creation_tokens = usage.get("cache_creation_input_tokens", 0)
            last_error = None
            break

        except Exception as exc:
            if not _is_retryable(exc):
                last_error = str(exc)
                logger.error(
                    f"[LLM] Non-retryable error for stage={stage}: {type(exc).__name__} — "
                    "not retrying (would waste budget on a permanent failure)"
                )
                break

            last_error = str(exc)
            if attempt < MAX_RETRIES:
                backoff = 2 ** attempt
                logger.warning(
                    f"[LLM] Attempt {attempt + 1} failed (retryable) "
                    f"for stage={stage}: {type(exc).__name__} — retrying in {backoff}s"
                )
                time.sleep(backoff)
            else:
                logger.error(f"[LLM] All retries exhausted for stage={stage}: {exc}")

    # Track spend and enforce budget (after the call, not before, so we always log)
    if input_tokens or output_tokens:
        pricing = get_pricing(model)
        # Cache reads are billed at ~10% of normal input rate; creation at full rate
        billed_input = (input_tokens - cache_read_tokens) + (cache_read_tokens * 0.1)
        cost_usd = (
            billed_input / 1_000_000 * pricing["input"]
            + output_tokens / 1_000_000 * pricing["output"]
        )
        if cache_read_tokens:
            logger.info(
                f"[CACHE] stage={stage} cache_read={cache_read_tokens}t "
                f"cache_create={cache_creation_tokens}t "
                f"(saved ~${(cache_read_tokens * pricing['input'] * 0.9 / 1e6):.4f})"
            )
        try:
            _check_and_record_spend(cost_usd)
        except BudgetExceededError:
            _write_log_entry(
                stage, source_url, content_ids, model, prompt_hash,
                input_artifacts, output_artifact, effective_max_tokens,
                input_tokens, output_tokens, cache_read_tokens,
                cache_creation_tokens, "BudgetExceeded",
            )
            raise

    _write_log_entry(
        stage, source_url, content_ids, model, prompt_hash,
        input_artifacts, output_artifact, effective_max_tokens,
        input_tokens, output_tokens, cache_read_tokens,
        cache_creation_tokens, last_error,
    )
    return result


def _write_log_entry(
    stage: str,
    source_url: Optional[str],
    content_ids: Optional[list[str]],
    model: str,
    prompt_hash: str,
    input_artifacts: list[str],
    output_artifact: str,
    max_tokens_used: int,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_creation_tokens: int,
    error: Optional[str],
) -> None:
    entry = {
        "stage": stage,
        "source_url": source_url,
        "content_ids": content_ids or [],
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "provider": "openrouter",
        "model": model,
        "prompt_hash": prompt_hash,
        "input_artifacts": input_artifacts,
        "output_artifact": output_artifact,
        "estimated_input_tokens": input_tokens,
        "estimated_output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_creation_tokens": cache_creation_tokens,
        "max_tokens_used": max_tokens_used,
        "error": error,
    }
    with _log_lock:
        with open(LLM_CALLS, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")


# ── JSON parsing with truncation recovery ──────────────────────────────────────
def parse_json_response(text: str) -> dict | list:
    """
    Strip markdown fences and parse JSON.
    If parsing fails (truncated due to max_tokens), attempt structural recovery.
    Returns empty dict only when all recovery strategies fail.
    """
    text = text.strip()

    if text.startswith("```"):
        parts = text.split("```")
        inner = parts[1] if len(parts) > 1 else ""
        if inner.startswith("json"):
            inner = inner[4:]
        text = inner.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        logger.warning(
            f"[JSON] Primary parse failed ({e}), attempting recovery on "
            f"{len(text)} char response..."
        )

    recovered = _recover_partial_json(text)
    if recovered:
        logger.info("[JSON] Recovery succeeded")
        return recovered

    logger.error(f"[JSON] All parse strategies failed. Raw (first 400 chars): {text[:400]}")
    return {}


def _recover_partial_json(text: str) -> dict | list | None:
    outer_match = re.match(r'\s*\{\s*"(\w+)"\s*:\s*\[', text)
    if not outer_match:
        bare_match = re.match(r'\s*\[', text)
        if bare_match:
            return _extract_objects_from_array(text[bare_match.end() - 1:])
        return None

    key = outer_match.group(1)
    objects = _extract_objects_from_array("[" + text[outer_match.end():])
    if objects is not None:
        logger.warning(
            f"[JSON] Recovered {len(objects)} complete objects from truncated "
            f'"{key}" array (response was cut mid-JSON)'
        )
        return {key: objects}
    return None


def _extract_objects_from_array(text: str) -> list | None:
    objects = []
    depth = 0
    obj_start = -1
    in_string = False
    escape_next = False

    for i, ch in enumerate(text):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue

        if ch == "{":
            if depth == 0:
                obj_start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and obj_start >= 0:
                try:
                    obj = json.loads(text[obj_start: i + 1])
                    objects.append(obj)
                    obj_start = -1
                except json.JSONDecodeError:
                    obj_start = -1

    return objects if objects else None


# ── Span validation (hallucination guard) ──────────────────────────────────────
def validate_spans_against_corpus(evidence_list: list[dict], corpus: str) -> list[dict]:
    """
    Code-enforced hallucination guard: verify evidence spans exist in source corpus.
    Uses config.defaults.span_overlap_threshold (default 0.5) word-overlap threshold.
    """
    if not corpus:
        return evidence_list

    threshold = get_span_overlap_threshold()
    corpus_lower = corpus.lower()
    validated = []

    for ev in evidence_list:
        span = ev.get("source_span", "").strip()
        if not span or len(span) < 5:
            validated.append(ev)
            continue

        words = [w for w in span.lower().split() if len(w) > 3]
        if not words:
            validated.append(ev)
            continue

        matches = sum(1 for w in words if w in corpus_lower)
        overlap = matches / len(words)

        if overlap >= threshold:
            validated.append(ev)
        else:
            logger.warning(
                f"[HALLUCINATION GUARD] Rejected span with {overlap:.0%} word overlap: "
                f"'{span[:60]}'"
            )

    return validated
