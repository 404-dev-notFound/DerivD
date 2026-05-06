"""Stage — CONTENT_EXTRACTED: Parse HTML into structured records. Deterministic, no LLM."""
from __future__ import annotations
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.content_utils import extract_content_items, source_name_from_url

logger = logging.getLogger(__name__)


def extract_content(html_map: dict[str, str]) -> list[dict]:
    """
    Parse HTML from each source using BeautifulSoup.
    Returns flat list of raw content dicts (validated later in CONTENT_NORMALISED).
    """
    all_items: list[dict] = []

    for url, html in html_map.items():
        name = source_name_from_url(url)
        items = extract_content_items(html, url, name)
        all_items.extend(items)
        logger.info(f"[CONTENT_EXTRACTED] {len(items)} items from {url}")

    logger.info(f"[CONTENT_EXTRACTED] Total raw items: {len(all_items)}")
    return all_items
