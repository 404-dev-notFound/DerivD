"""
Stage — CONTENT_NORMALISED: Dedup, validate schema, write extracted_content.json.
Deterministic — no LLM.
"""
from __future__ import annotations
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.content_utils import compute_content_hash
from utils.models import ContentItem, NumericalData
from utils.paths import EXTRACTED_CONTENT

logger = logging.getLogger(__name__)
OUTPUT_PATH = EXTRACTED_CONTENT


def normalise_content(raw_items: list[dict]) -> list[dict]:
    """
    SHA-256 dedup + Pydantic validation → write extracted_content.json.
    Skips duplicates and invalid items (logged). Returns validated list.
    """
    seen_hashes: set[str] = set()
    validated: list[dict] = []
    skipped_dup = 0
    skipped_invalid = 0

    for raw in raw_items:
        h = compute_content_hash(raw)
        if h in seen_hashes:
            skipped_dup += 1
            continue
        seen_hashes.add(h)

        try:
            num_data = [NumericalData(**nd) for nd in raw.get("numerical_data", [])]
            item = ContentItem(
                **{k: v for k, v in raw.items() if k != "numerical_data"},
                numerical_data=num_data,
            )
            out = item.model_dump(exclude={"content_hash"})
            out["numerical_data"] = [nd.model_dump() for nd in num_data]
            validated.append(out)
        except Exception as e:
            skipped_invalid += 1
            logger.warning(
                f"[CONTENT_NORMALISED] Skipped invalid item {raw.get('content_id')}: {e}"
            )

    logger.info(
        f"[CONTENT_NORMALISED] {len(validated)} kept, "
        f"{skipped_dup} duplicates, {skipped_invalid} invalid"
    )

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(validated, f, ensure_ascii=False, indent=2)
    logger.info(f"[CONTENT_NORMALISED] Written → {OUTPUT_PATH}")
    return validated
