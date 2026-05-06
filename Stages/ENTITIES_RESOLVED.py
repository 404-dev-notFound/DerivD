"""
Stage — ENTITIES_RESOLVED: LLM alias resolution → canonical entity registry → entities.json.
Deterministic: prompt construction, Pydantic validation, JSON output.
LLM: alias grouping and canonical name selection.
"""
from __future__ import annotations
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.llm_client import llm_call, parse_json_response
from utils.models import Entity, SourceMention

logger = logging.getLogger(__name__)
OUTPUT_PATH = "entities.json"

SYSTEM_RESOLVE_ENTITIES = """You are a financial entity resolution engine. Group raw entity mentions into canonical entities, resolving all aliases.

Resolution examples:
- "The Fed" = "Federal Reserve" = "US central bank" → canonical: "Federal Reserve"
- "greenback" = "USD" = "US dollar" → canonical: "USD"
- "EUR/USD" = "EURUSD" = "euro-dollar" → canonical: "EUR/USD"
- "NFP" = "nonfarm payrolls" → canonical: "Nonfarm Payrolls"

Rules:
- Assign entity_id in format ENT_001, ENT_002, ...
- Choose the most specific, widely-recognized canonical name
- List ALL alias variants in the aliases array
- resolution_confidence: 0.0–1.0 (be conservative; 1.0 only for unambiguous)
- Entities below 0.6 confidence will be QA-flagged automatically by code

STRICT ANTI-HALLUCINATION RULES:
1. Only reference content_ids and source_urls from the provided mention data.
2. Do NOT invent source mentions not present in the input.
3. source_span must be copied verbatim from the mention input.

Output valid JSON only. No markdown fences.
Schema: {"entities":[{"entity_id":"string","canonical_name":"string","entity_type":"string","aliases":["string"],"source_mentions":[{"content_id":"string","source_url":"string","mention_text":"string","source_span":"string"}],"resolution_confidence":0.0}]}"""


def resolve_entities(raw_mentions: list[dict]) -> list[dict]:
    """
    LLM resolves entity mentions into canonical entities.
    Pydantic validates output. Invalid/hallucinated references dropped by code.
    Falls back to empty entities on total LLM failure.
    """
    if not raw_mentions:
        logger.warning("[ENTITIES_RESOLVED] No mentions; writing empty entities.json")
        _write([], 0)
        return []

    valid_content_ids = {m.get("content_id") for m in raw_mentions}
    user_content = json.dumps({"mentions": raw_mentions}, ensure_ascii=False)

    try:
        raw_response = llm_call(
            stage="entity_resolution",
            system=SYSTEM_RESOLVE_ENTITIES,
            user_content=user_content,
            input_artifacts=["extracted_content.json"],
            output_artifact=OUTPUT_PATH,
        )
        parsed = parse_json_response(raw_response)
        raw_entities = parsed.get("entities", []) if isinstance(parsed, dict) else []
    except Exception as e:
        logger.error(
            f"[ENTITIES_RESOLVED] LLM failed: {e}. Writing empty entities.json."
        )
        _write([], 0)
        return []

    validated = _validate(raw_entities, valid_content_ids)
    _write(validated, len(raw_entities))
    return validated


def _validate(raw_entities: list[dict], valid_ids: set[str]) -> list[dict]:
    """Pydantic schema check + content_id verification (hallucination guard)."""
    validated = []
    for raw in raw_entities:
        try:
            clean_mentions = []
            for sm in raw.get("source_mentions", []):
                if sm.get("content_id") in valid_ids:
                    clean_mentions.append(SourceMention(**sm))
                else:
                    logger.warning(
                        f"[ENTITIES_RESOLVED] Dropped mention with invalid "
                        f"content_id={sm.get('content_id')} (hallucination guard)"
                    )
            raw["source_mentions"] = [m.model_dump() for m in clean_mentions]
            entity = Entity(**raw)
            validated.append(entity.model_dump())
        except Exception as e:
            logger.warning(
                f"[ENTITIES_RESOLVED] Skipped invalid entity "
                f"{raw.get('entity_id')}: {e}"
            )
    return validated


def _write(entities: list[dict], total_raw: int) -> None:
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(entities, f, ensure_ascii=False, indent=2)
    logger.info(
        f"[ENTITIES_RESOLVED] {len(entities)}/{total_raw} entities written → {OUTPUT_PATH}"
    )
