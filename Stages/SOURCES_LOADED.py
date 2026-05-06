"""Stage — SOURCES_LOADED: Read and validate sources.json. Deterministic."""
from __future__ import annotations
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger(__name__)


def load_sources(path: str = "sources.json") -> list[str]:
    """Read sources.json and return list of URLs. Raises on missing/invalid file."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"sources.json not found at: {os.path.abspath(path)}")

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict) or "sources" not in data:
        raise ValueError("sources.json must be {'sources': [...]}")

    sources = data["sources"]
    if not isinstance(sources, list) or not sources:
        raise ValueError("sources.json 'sources' must be a non-empty list of URLs")

    for s in sources:
        if not isinstance(s, str) or not s.startswith("http"):
            raise ValueError(f"Invalid source URL: {s!r}")

    logger.info(f"[SOURCES_LOADED] {len(sources)} source(s) loaded")
    return sources
