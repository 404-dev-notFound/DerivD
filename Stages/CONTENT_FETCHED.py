"""Stage — CONTENT_FETCHED: HTTP fetch for each source URL. Deterministic."""
from __future__ import annotations
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.content_utils import fetch_source

logger = logging.getLogger(__name__)


def fetch_content(sources: list[str]) -> tuple[dict[str, str], list[str]]:
    """
    Fetch HTML for every URL. Returns (html_by_url, fetch_errors).
    Never raises — failures are logged and pipeline continues with what succeeded.
    At least one source must succeed or RuntimeError is raised.
    """
    html_map: dict[str, str] = {}
    errors: list[str] = []

    for url in sources:
        html, err = fetch_source(url)
        if html is not None:
            html_map[url] = html
        else:
            errors.append(err or f"Unknown error fetching {url}")

    if not html_map:
        raise RuntimeError(
            f"All {len(sources)} source(s) failed. Cannot continue pipeline.\n"
            + "\n".join(errors)
        )

    logger.info(
        f"[CONTENT_FETCHED] {len(html_map)}/{len(sources)} OK, "
        f"{len(errors)} failed"
    )
    for err in errors:
        logger.warning(f"[CONTENT_FETCHED][FETCH_FAILURE] {err}")

    return html_map, errors
