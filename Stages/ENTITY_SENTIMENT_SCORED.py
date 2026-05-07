"""
Stage — ENTITY_SENTIMENT_SCORED: Per-entity sentiment scoring via LLM → entity_sentiment.json.
Deterministic: prompt construction, Pydantic validation, hallucination span guard.
LLM: sentiment reasoning and evidence selection.
"""
from __future__ import annotations
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.llm_client import llm_call, parse_json_response, validate_spans_against_corpus
from utils.models import EntitySentiment, SentimentEvidence
from utils.config import get_max_tokens_for_stage, get_stage_param
from utils.paths import ENTITIES, EXTRACTED_CONTENT, ENTITY_SENTIMENT
from utils.content_utils import sanitise_url_for_prompt
from utils.artifact_store import atomic_write_json

logger = logging.getLogger(__name__)
OUTPUT_PATH = ENTITY_SENTIMENT
STAGE = "entity_sentiment_scoring"

SYSTEM_SCORE_SENTIMENT = """You are a financial sentiment analysis engine.

Score sentiment PER ENTITY, not per page. One article may be bullish on one entity and bearish on another.

Sentiment values: bullish | bearish | neutral | mixed
sentiment_score: -1.0 (strongly bearish) to +1.0 (strongly bullish)
confidence: 0.0 to 1.0

For each entity, review ALL content items that mention it and determine:
1. The overall sentiment signal across all sources
2. Specific source_spans that justify the signal (verbatim from provided content)
3. A brief reason explaining why each evidence item supports the sentiment

STRICT ANTI-HALLUCINATION RULES:
1. ONLY cite source_spans that are verbatim excerpts from the content provided below.
2. ONLY reference content_ids that appear in the provided content.
3. NEVER invent prices, figures, forecasts, or market positions not in the source content.
4. If no relevant content exists for an entity, set sentiment="neutral", confidence=0.3, evidence=[].
5. Unsupported sentiment claims are NOT acceptable.

Output valid JSON only. No markdown fences.
Schema: {"entity_sentiment":[{"entity_id":"string","canonical_name":"string","sentiment":"bullish|bearish|neutral|mixed","sentiment_score":0.0,"confidence":0.0,"evidence":[{"content_id":"string","source_url":"string","source_span":"string","reason":"string"}]}]}"""


def score_entity_sentiment(
    entities: list[dict], content_items: list[dict]
) -> list[dict]:
    """
    LLM scores sentiment per entity using the content corpus.
    Hallucination guard: validates evidence spans exist in corpus.
    Fallback: returns empty list on total LLM failure.
    """
    if not entities:
        logger.warning("[ENTITY_SENTIMENT_SCORED] No entities; writing empty sentiment file")
        _write([])
        return []

    corpus = _build_corpus(content_items)
    valid_content_ids = {item["content_id"] for item in content_items}
    user_content = _build_user_content(entities, content_items)

    all_ids = [e["entity_id"] for e in entities]

    try:
        raw_response = llm_call(
            stage=STAGE,
            system=SYSTEM_SCORE_SENTIMENT,
            user_content=user_content,
            input_artifacts=[ENTITIES, EXTRACTED_CONTENT],
            output_artifact=OUTPUT_PATH,
            content_ids=list(valid_content_ids),
            max_tokens=get_max_tokens_for_stage(STAGE),
        )
        parsed = parse_json_response(raw_response)
        raw_sentiments = (
            parsed.get("entity_sentiment", []) if isinstance(parsed, dict) else []
        )
    except Exception as e:
        logger.error(
            f"[ENTITY_SENTIMENT_SCORED] LLM failed: {e}. Writing empty sentiment file."
        )
        _write([])
        return []

    valid_entity_ids = {e["entity_id"] for e in entities}
    validated = _validate(raw_sentiments, valid_entity_ids, valid_content_ids, corpus)
    _write(validated)
    return validated


def _validate(
    raw: list[dict],
    valid_entity_ids: set[str],
    valid_content_ids: set[str],
    corpus: str,
) -> list[dict]:
    """Pydantic validation + span hallucination guard + entity_id check."""
    validated = []
    for item in raw:
        if item.get("entity_id") not in valid_entity_ids:
            logger.warning(
                f"[ENTITY_SENTIMENT_SCORED] Skipped sentiment for unknown "
                f"entity_id={item.get('entity_id')} (hallucination guard)"
            )
            continue

        # Filter evidence to valid content_ids
        raw_evidence = [
            ev for ev in item.get("evidence", [])
            if ev.get("content_id") in valid_content_ids
        ]

        # Span hallucination guard (code-enforced)
        verified_evidence = validate_spans_against_corpus(raw_evidence, corpus)

        item["evidence"] = verified_evidence
        try:
            ev_models = [SentimentEvidence(**ev) for ev in verified_evidence]
            sent = EntitySentiment(
                **{k: v for k, v in item.items() if k != "evidence"},
                evidence=ev_models,
            )
            out = sent.model_dump()
            out["evidence"] = [ev.model_dump() for ev in ev_models]
            validated.append(out)
        except Exception as e:
            logger.warning(
                f"[ENTITY_SENTIMENT_SCORED] Invalid sentiment for "
                f"{item.get('entity_id')}: {e}"
            )
    logger.info(
        f"[ENTITY_SENTIMENT_SCORED] {len(validated)}/{len(raw)} sentiment records validated"
    )
    return validated


def _build_corpus(content_items: list[dict]) -> str:
    """Build flat text corpus for span verification."""
    parts = []
    for item in content_items:
        parts.append(item.get("title", ""))
        parts.append(item.get("body", ""))
    return " ".join(parts).lower()


def _build_user_content(entities: list[dict], content_items: list[dict]) -> str:
    mentions_cap = int(get_stage_param(STAGE, "mentions_cap_per_entity", 10))
    content_cap = int(get_stage_param(STAGE, "content_cap_per_mention_chars", 600))
    content_map: dict[str, dict] = {c["content_id"]: c for c in content_items}
    lines = ["Score sentiment for each entity below using ONLY the provided content.\n"]

    for entity in entities:
        lines.append(
            f"\n=== ENTITY: {entity['entity_id']} — {entity['canonical_name']} "
            f"(type: {entity.get('entity_type', 'unknown')}) ==="
        )
        mentions = entity.get("source_mentions", [])
        seen_content: set[str] = set()
        for mention in mentions[:mentions_cap]:
            cid = mention.get("content_id")
            if cid in seen_content or cid not in content_map:
                continue
            seen_content.add(cid)
            item = content_map[cid]
            body = (item.get("title", "") + " " + item.get("body", "")).strip()[:content_cap]
            lines.append(
                f"[content_id={cid} source_url={sanitise_url_for_prompt(item['source_url'])}]\n{body}\n"
            )

    return "\n".join(lines)


def _write(records: list[dict]) -> None:
    atomic_write_json(OUTPUT_PATH, records)
    logger.info(
        f"[ENTITY_SENTIMENT_SCORED] Written -> {OUTPUT_PATH} ({len(records)} records)"
    )
