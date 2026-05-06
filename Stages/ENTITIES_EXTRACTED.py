"""
Stage — ENTITIES_EXTRACTED: LLM batch call to identify financial entity mentions.
Deterministic: batching, prompt construction, JSON parsing, content_id validation.
LLM: deciding which spans are financial entities and what type they are.
"""
from __future__ import annotations
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.llm_client import llm_call, parse_json_response
from utils.content_utils import sanitise_text
from utils.config import get_stage_param

logger = logging.getLogger(__name__)

STAGE = "entity_extraction"

SYSTEM_EXTRACT_ENTITIES = """You are a financial entity extraction engine. Extract all financial entities from the provided content.

Entity types: currency, currency_pair, index, commodity, central_bank, economic_indicator, company, person, country, event, other

For each entity found, record:
- mention_text: exact text as it appears in source
- entity_type: one of the types above
- content_id: the content_id of the item where it was found
- source_url: the source_url of that item
- source_span: 10-15 words of verbatim context around the mention

STRICT ANTI-HALLUCINATION RULES:
1. Only extract entities EXPLICITLY present in the content below.
2. Do NOT invent, infer, or assume any entities not mentioned in the text.
3. source_span MUST be a verbatim excerpt from the provided content.
4. content_id MUST match exactly one of the content_ids in the input.
5. If no financial entities are found, return {"entities": []}.

Output valid JSON only. No markdown fences.
Schema: {"entities": [{"mention_text":"string","entity_type":"string","content_id":"string","source_url":"string","source_span":"string"}]}"""

def extract_entities(content_items: list[dict]) -> list[dict]:
    """
    Batch content → LLM entity mention extraction.
    Returns flat list of raw entity mention dicts.
    Failures produce empty batch (code-enforced fallback, not model-enforced).
    """
    batch_size = int(get_stage_param(STAGE, "batch_size", 35))
    char_cap = int(get_stage_param(STAGE, "per_item_char_cap", 1000))

    all_mentions: list[dict] = []
    batches = [
        content_items[i:i + batch_size]
        for i in range(0, len(content_items), batch_size)
    ]
    logger.info(
        f"[ENTITIES_EXTRACTED] {len(content_items)} items → {len(batches)} batch(es) "
        f"(batch_size={batch_size})"
    )

    for idx, batch in enumerate(batches):
        batch_ids = {item["content_id"] for item in batch}
        user_text = _build_batch_text(batch, char_cap)
        content_ids = list(batch_ids)

        try:
            raw_response = llm_call(
                stage=STAGE,
                system=SYSTEM_EXTRACT_ENTITIES,
                user_content=user_text,
                input_artifacts=["extracted_content.json"],
                output_artifact="entities.json",
                content_ids=content_ids,
            )
            parsed = parse_json_response(raw_response)
            mentions = (
                parsed.get("entities", []) if isinstance(parsed, dict) else []
            )

            # Code-enforced: only keep mentions with valid content_ids
            valid = [m for m in mentions if m.get("content_id") in batch_ids]
            dropped = len(mentions) - len(valid)
            if dropped:
                logger.warning(
                    f"[ENTITIES_EXTRACTED] Batch {idx}: dropped {dropped} "
                    f"mentions referencing unknown content_ids (hallucination guard)"
                )

            all_mentions.extend(valid)
            logger.info(
                f"[ENTITIES_EXTRACTED] Batch {idx}: {len(valid)} mentions"
            )
        except Exception as e:
            logger.error(
                f"[ENTITIES_EXTRACTED] Batch {idx} failed: {e}. "
                "Using empty batch (code-enforced fallback)."
            )

    logger.info(f"[ENTITIES_EXTRACTED] Total mentions: {len(all_mentions)}")
    return all_mentions


def _build_batch_text(batch: list[dict], char_cap: int = 1000) -> str:
    lines = [
        "Extract all financial entity mentions from the items below.\n"
    ]
    for item in batch:
        title = sanitise_text(item.get("title", "").strip())
        body = sanitise_text(item.get("body", "").strip())
        text = f"{title} {body}".strip()[:char_cap]
        lines.append(
            f"---\ncontent_id: {item['content_id']}\n"
            f"source_url: {item['source_url']}\n"
            f"text: {text}\n"
        )
    return "\n".join(lines)
