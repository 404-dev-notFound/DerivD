"""
Stage — ENTITIES_RESOLVED: LLM alias resolution → canonical entity registry → entities.json.
Deterministic: chunking, prompt construction, Pydantic validation, dedup, JSON output.
LLM: alias grouping and canonical name selection.

Fix: large mention sets are chunked (≤25 per call) to prevent JSON truncation.
     Each chunk uses max_tokens=8192 to accommodate large entity lists.
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
CHUNK_SIZE = 20       # max mentions per LLM call to stay within token budget
MAX_TOKENS = 8192     # entity resolution produces large JSON; needs headroom

SYSTEM_RESOLVE_ENTITIES = """You are a financial entity resolution engine. Group raw entity mentions into canonical entities, resolving all aliases.

Resolution examples:
- "The Fed" = "Federal Reserve" = "US central bank" → canonical: "Federal Reserve"
- "greenback" = "USD" = "US dollar" → canonical: "USD"
- "EUR/USD" = "EURUSD" = "euro-dollar" → canonical: "EUR/USD"
- "NFP" = "nonfarm payrolls" → canonical: "Nonfarm Payrolls"

Rules:
- Assign entity_id in format ENT_001, ENT_002, ... (unique within this batch)
- Choose the most specific, widely-recognized canonical name
- List ALL alias variants in the aliases array
- resolution_confidence: 0.0–1.0 (be conservative; 1.0 only for unambiguous)
- Entities below 0.6 confidence will be QA-flagged automatically by code

Output valid JSON only. No markdown fences. Keep responses concise.
Schema: {"entities":[{"entity_id":"string","canonical_name":"string","entity_type":"string","aliases":["string"],"source_mentions":[{"content_id":"string","source_url":"string","mention_text":"string","source_span":"string"}],"resolution_confidence":0.0}]}"""


def resolve_entities(raw_mentions: list[dict]) -> list[dict]:
    """
    LLM resolves entity mentions into canonical entities.
    Chunks large mention sets to prevent JSON truncation.
    Pydantic validates each entity. Hallucinated content_ids dropped by code.
    Falls back to empty entities on total LLM failure.
    """
    if not raw_mentions:
        logger.warning("[ENTITIES_RESOLVED] No mentions; writing empty entities.json")
        _write([], 0)
        return []

    valid_content_ids = {m.get("content_id") for m in raw_mentions}
    chunks = [
        raw_mentions[i : i + CHUNK_SIZE]
        for i in range(0, len(raw_mentions), CHUNK_SIZE)
    ]
    logger.info(
        f"[ENTITIES_RESOLVED] {len(raw_mentions)} mentions → "
        f"{len(chunks)} chunk(s) of ≤{CHUNK_SIZE}"
    )

    all_raw_entities: list[dict] = []
    for idx, chunk in enumerate(chunks):
        chunk_entities = _resolve_chunk(chunk, idx)
        all_raw_entities.extend(chunk_entities)

    # Re-number entity_ids sequentially across all chunks (avoid ENT_001 collisions)
    _renumber_entity_ids(all_raw_entities)

    # Deduplicate by canonical_name (same entity may appear in multiple chunks)
    deduped = _dedup_by_canonical_name(all_raw_entities)

    validated = _validate(deduped, valid_content_ids)
    _write(validated, len(all_raw_entities))
    return validated


def _resolve_chunk(chunk: list[dict], chunk_idx: int) -> list[dict]:
    """Run one LLM resolution call for a chunk of mentions."""
    user_content = json.dumps({"mentions": chunk}, ensure_ascii=False)
    try:
        raw_response = llm_call(
            stage="entity_resolution",
            system=SYSTEM_RESOLVE_ENTITIES,
            user_content=user_content,
            input_artifacts=["extracted_content.json"],
            output_artifact=OUTPUT_PATH,
            max_tokens=MAX_TOKENS,
        )
        parsed = parse_json_response(raw_response)
        entities = parsed.get("entities", []) if isinstance(parsed, dict) else []
        logger.info(
            f"[ENTITIES_RESOLVED] Chunk {chunk_idx}: {len(entities)} entities resolved"
        )
        return entities
    except Exception as e:
        logger.error(
            f"[ENTITIES_RESOLVED] Chunk {chunk_idx} failed: {e}. Skipping chunk."
        )
        return []


def _renumber_entity_ids(entities: list[dict]) -> None:
    """Assign globally unique sequential entity_ids across all chunks (in-place)."""
    for i, ent in enumerate(entities, start=1):
        ent["entity_id"] = f"ENT_{i:03d}"


def _dedup_by_canonical_name(entities: list[dict]) -> list[dict]:
    """
    Merge entities with the same canonical_name (case-insensitive).
    Keeps the first occurrence; merges aliases and source_mentions from duplicates.
    """
    seen: dict[str, dict] = {}
    for ent in entities:
        key = ent.get("canonical_name", "").lower().strip()
        if not key:
            continue
        if key not in seen:
            seen[key] = dict(ent)
            seen[key]["aliases"] = list(set(ent.get("aliases", [])))
            seen[key]["source_mentions"] = list(ent.get("source_mentions", []))
        else:
            # Merge aliases and mentions from duplicate
            existing = seen[key]
            existing["aliases"] = list(
                set(existing["aliases"]) | set(ent.get("aliases", []))
            )
            existing["source_mentions"].extend(ent.get("source_mentions", []))
            # Keep the higher confidence score
            if ent.get("resolution_confidence", 0) > existing.get("resolution_confidence", 0):
                existing["resolution_confidence"] = ent["resolution_confidence"]

    deduped = list(seen.values())
    dropped = len(entities) - len(deduped)
    if dropped:
        logger.info(f"[ENTITIES_RESOLVED] Merged {dropped} duplicate canonical entities")
    return deduped


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
    logger.info(f"[ENTITIES_RESOLVED] {len(validated)}/{len(raw_entities)} entities passed validation")
    return validated


def _write(entities: list[dict], total_raw: int) -> None:
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(entities, f, ensure_ascii=False, indent=2)
    logger.info(
        f"[ENTITIES_RESOLVED] {len(entities)}/{total_raw} entities written → {OUTPUT_PATH}"
    )
