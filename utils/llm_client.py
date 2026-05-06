"""
OpenRouter LLM client.

Deterministic responsibilities (this file):
- Making HTTP calls to OpenRouter
- Logging every call to llm_calls.jsonl
- Retrying on transient errors (code-enforced, not model-enforced)
- Parsing JSON responses with fallback (code-enforced)
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
import datetime
from typing import Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

OPENROUTER_BASE = "https://openrouter.ai/api/v1/chat/completions"
MAX_RETRIES = 2


def _get_client() -> httpx.Client:
    return httpx.Client(timeout=120.0)


def llm_call(
    stage: str,
    system: str,
    user_content: str,
    input_artifacts: list[str],
    output_artifact: str,
    source_url: Optional[str] = None,
    content_ids: Optional[list[str]] = None,
) -> str:
    """Make one LLM call via OpenRouter, log it, return response text."""
    api_key = os.environ["OPENROUTER_API_KEY"]
    model = os.environ.get("LLM_MODEL", "anthropic/claude-sonnet-4-5")
    max_tokens = int(os.environ.get("MAX_TOKENS", 4096))

    prompt_hash = hashlib.sha256((system + user_content).encode()).hexdigest()[:16]

    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
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
        "error": last_error,
    }
    with open("llm_calls.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry) + "\n")

    return result


def parse_json_response(text: str) -> dict | list:
    """Strip markdown fences and parse JSON. Returns empty dict on failure."""
    text = text.strip()
    if text.startswith("```"):
        parts = text.split("```")
        # parts[1] is the content after first fence
        inner = parts[1] if len(parts) > 1 else ""
        if inner.startswith("json"):
            inner = inner[4:]
        text = inner.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        logger.error(f"[JSON] Failed to parse LLM response: {e}\nRaw: {text[:300]}")
        return {}


def validate_spans_against_corpus(evidence_list: list[dict], corpus: str) -> list[dict]:
    """
    Code-enforced hallucination guard.
    Checks that each evidence span's key words appear in the source corpus.
    Removes spans that cannot be verified. Logs rejections.
    """
    if not corpus:
        return evidence_list

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

        if overlap >= 0.5:
            validated.append(ev)
        else:
            logger.warning(
                f"[HALLUCINATION GUARD] Rejected span with {overlap:.0%} overlap: '{span[:60]}'"
            )

    return validated
