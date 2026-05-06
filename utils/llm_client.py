"""
OpenRouter LLM client.

Deterministic responsibilities (this file):
- Loading anti-hallucination rules from hallucination.md (single source of truth)
- Making HTTP calls to OpenRouter with per-call max_tokens override
- Logging every call to llm_calls.jsonl
- Retrying on transient errors (code-enforced, not model-enforced)
- Recovering from truncated JSON responses (code-enforced)
- Validating evidence spans against source corpus (hallucination guard)

NOT done here:
- Deciding what to extract (LLM prompt responsibility)
- Deciding what makes a valid entity (LLM prompt responsibility)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import datetime
from typing import Optional

import httpx
from dotenv import load_dotenv

from utils.config import (
    get_max_tokens_for_stage,
    get_model_for_stage,
    get_span_overlap_threshold,
)
from utils.paths import LLM_CALLS

load_dotenv()

logger = logging.getLogger(__name__)

OPENROUTER_BASE = "https://openrouter.ai/api/v1/chat/completions"
MAX_RETRIES = 2

# ── Hallucination skill — loaded once from hallucination.md ───────────────────
# hallucination.md is the single source of truth for anti-hallucination rules.
# These rules are prepended to EVERY system prompt automatically.

def _load_hallucination_rules() -> str:
    """Extract the Prompt-Level Constraints block from hallucination.md."""
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
        # Extract the fenced block under "## Prompt-Level Constraints"
        match = re.search(
            r"## Prompt-Level Constraints.*?```(.*?)```",
            content,
            re.DOTALL,
        )
        if match:
            rules = match.group(1).strip()
            logger.info("[HALLUCINATION] Injecting rules from hallucination.md into all LLM calls")
            return rules
    except Exception as e:
        logger.warning(f"[HALLUCINATION] Could not load hallucination.md: {e}")
    return ""


_HALLUCINATION_RULES: str = _load_hallucination_rules()


def _inject_rules(system: str) -> str:
    """Prepend hallucination rules to any system prompt."""
    if not _HALLUCINATION_RULES:
        return system
    return f"{_HALLUCINATION_RULES}\n\n---\n\n{system}"


# ── HTTP client ────────────────────────────────────────────────────────────────

def _get_client() -> httpx.Client:
    return httpx.Client(timeout=180.0)


# ── LLM call ──────────────────────────────────────────────────────────────────

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
    Make one LLM call via OpenRouter, log it to llm_calls.jsonl, return response text.

    Hallucination rules from hallucination.md are automatically prepended to
    the system prompt. max_tokens defaults to the MAX_TOKENS env var but can
    be overridden per-call for stages that produce large JSON responses.
    """
    api_key = os.environ["OPENROUTER_API_KEY"]
    # Tiered routing: config.json maps each stage to a model tier (Haiku/Sonnet/Opus)
    # to minimise cost on classification-heavy stages while keeping reasoning quality
    # where it matters. Env var LLM_MODEL_<STAGE> overrides config for targeted ops.
    model = get_model_for_stage(stage)
    effective_max_tokens = max_tokens or get_max_tokens_for_stage(stage)

    # Inject hallucination rules (from hallucination.md) into every system prompt
    effective_system = _inject_rules(system)

    prompt_hash = hashlib.sha256(
        (effective_system + user_content).encode()
    ).hexdigest()[:16]

    payload = {
        "model": model,
        "max_tokens": effective_max_tokens,
        "messages": [
            {"role": "system", "content": effective_system},
            {"role": "user", "content": user_content},
        ],
    }

    result = ""
    input_tokens = 0
    output_tokens = 0
    last_error = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            with _get_client() as client:
                resp = client.post(
                    OPENROUTER_BASE,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                        "HTTP-Referer": "https://github.com/financial-intelligence-pipeline",
                    },
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                result = data["choices"][0]["message"]["content"]
                usage = data.get("usage", {})
                input_tokens = usage.get("prompt_tokens", 0)
                output_tokens = usage.get("completion_tokens", 0)
                last_error = None
                break
        except Exception as e:
            last_error = str(e)
            logger.warning(f"[LLM] Attempt {attempt + 1} failed for stage={stage}: {e}")
            if attempt == MAX_RETRIES:
                logger.error(f"[LLM] All retries exhausted for stage={stage}: {e}")

    log_entry = {
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
        "max_tokens_used": effective_max_tokens,
        "error": last_error,
    }
    with open(LLM_CALLS, "a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry) + "\n")

    return result


# ── JSON parsing with truncation recovery ─────────────────────────────────────

def parse_json_response(text: str) -> dict | list:
    """
    Strip markdown fences and parse JSON.
    If parsing fails (e.g. truncated due to max_tokens), attempt structural recovery.
    Returns empty dict only when all recovery strategies fail.
    """
    text = text.strip()

    # Strip markdown code fences
    if text.startswith("```"):
        parts = text.split("```")
        inner = parts[1] if len(parts) > 1 else ""
        if inner.startswith("json"):
            inner = inner[4:]
        text = inner.strip()

    # Happy path
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        logger.warning(
            f"[JSON] Primary parse failed ({e}), attempting recovery on "
            f"{len(text)} char response..."
        )

    # Recovery: extract all complete JSON objects from a truncated array
    recovered = _recover_partial_json(text)
    if recovered:
        logger.info(f"[JSON] Recovery succeeded")
        return recovered

    logger.error(f"[JSON] All parse strategies failed. Raw (first 400 chars): {text[:400]}")
    return {}


def _recover_partial_json(text: str) -> dict | list | None:
    """
    Recover a truncated JSON response of the form {"key": [...]}.

    Strategy: scan for complete JSON objects inside the outermost array,
    collect all that parse successfully, and re-wrap them.
    This handles the common case where max_tokens cuts the response mid-object.
    """
    # Find the outer key name and the start of its array value
    outer_match = re.match(r'\s*\{\s*"(\w+)"\s*:\s*\[', text)
    if not outer_match:
        # Try bare array
        bare_match = re.match(r'\s*\[', text)
        if bare_match:
            return _extract_objects_from_array(text[bare_match.end() - 1:])
        return None

    key = outer_match.group(1)
    array_content = text[outer_match.end():]  # everything after the opening [
    objects = _extract_objects_from_array("[" + array_content)

    if objects is not None:
        logger.warning(
            f"[JSON] Recovered {len(objects)} complete objects from truncated "
            f'"{key}" array (response was cut mid-JSON)'
        )
        return {key: objects}
    return None


def _extract_objects_from_array(text: str) -> list | None:
    """
    Scan text for complete top-level JSON objects and return them as a list.
    Handles truncation by ignoring the incomplete final object.
    """
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
                    obj = json.loads(text[obj_start : i + 1])
                    objects.append(obj)
                    obj_start = -1
                except json.JSONDecodeError:
                    obj_start = -1  # malformed, skip

    return objects if objects else None


# ── Span validation (hallucination guard) ─────────────────────────────────────

def validate_spans_against_corpus(evidence_list: list[dict], corpus: str) -> list[dict]:
    """
    Code-enforced hallucination guard: verify evidence spans exist in source corpus.
    Uses config.defaults.span_overlap_threshold (default 0.5) word-overlap threshold.
    Rejects spans that can't be verified.
    Logs every rejection with [HALLUCINATION GUARD] prefix for auditability.
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
