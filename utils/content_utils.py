"""
Deterministic content utilities — no LLM involved.
Responsibilities: HTTP fetch, BeautifulSoup extraction, numerical regex, SHA-256 hashing.
"""
from __future__ import annotations

import hashlib
import re
import uuid
import datetime
import logging
from urllib.parse import urlparse
from typing import Optional

import ipaddress

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; FinancialIntelligenceBot/1.0)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

NUMERICAL_PATTERN = re.compile(
    r"(?P<value>[-+]?\d[\d,]*\.?\d*\s*(?:%|bps|bp|pct|percent)?)"
    r"(?:\s+(?P<unit>[A-Za-z][A-Za-z/]+))?"
)

_ALLOWED_SCHEMES = {"https"}
_BLOCKED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}

# Prompt injection — matches anywhere in the text, not just line-start.
# Previous version used ^ anchor so "Breaking: IGNORE PREVIOUS INSTRUCTIONS"
# on one line would pass through uncaught. Sub to [REDACTED] rather than
# drop lines so context is preserved and truncation artefacts don't confuse the LLM.
_INJECTION_PATTERN = re.compile(
    r"(ignore\s+(?:all\s+)?(?:previous|prior|above)\s+(?:instructions?|prompts?|context)|"
    r"disregard\s+(?:all\s+)?(?:previous|prior)\s+(?:instructions?|prompts?)|"
    r"forget\s+(?:all\s+)?(?:previous|prior)\s+(?:instructions?|context)|"
    r"system\s*:|assistant\s*:|<\s*/?system[>\s]|<\s*/?instruction[>\s])",
    re.IGNORECASE,
)

# Characters that must be percent-encoded when a URL is embedded in a prompt
# to prevent a crafted query-string from injecting LLM instructions.
_URL_UNSAFE_IN_PROMPT = re.compile(r"[^\w\-._~:/?#\[\]@!$&'()*+,;=%]")


def _validate_url(url: str) -> None:
    """SSRF guard: block private/internal IPs and disallowed schemes. Raises ValueError."""
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"Disallowed URL scheme: {parsed.scheme!r}")
    host = (parsed.hostname or "").lower()
    if host in _BLOCKED_HOSTS:
        raise ValueError(f"Blocked host: {host!r}")
    try:
        addr = ipaddress.ip_address(host)
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
            raise ValueError(f"Private/internal IP address blocked: {host!r}")
    except ValueError as exc:
        # Re-raise only if it's our own SSRF error, not the "not an IP" error
        if "Private" in str(exc) or "Blocked" in str(exc) or "Disallowed" in str(exc):
            raise


def sanitise_text(text: str) -> str:
    """
    Redact prompt-injection patterns anywhere in fetched text before LLM submission.
    Uses substitution (not line-dropping) so context is preserved and the LLM
    doesn't see suspicious gaps. Matches inline, not just at line boundaries.
    """
    return _INJECTION_PATTERN.sub("[REDACTED]", text)


def sanitise_url_for_prompt(url: str) -> str:
    """
    Encode any characters in a URL that could be used to inject LLM instructions
    via a crafted query string (e.g. ?q=IGNORE+PREVIOUS+INSTRUCTIONS).
    Applied before embedding source_url values into LLM prompts.
    """
    return _URL_UNSAFE_IN_PROMPT.sub(lambda m: f"%{ord(m.group()):02X}", url)


def fetch_source(url: str, timeout: int = 20) -> tuple[Optional[str], Optional[str]]:
    """Fetch raw HTML. Validates URL for SSRF first. Returns (html, error_message). Never raises."""
    try:
        _validate_url(url)
    except ValueError as e:
        err = f"URL rejected (security): {e}"
        logger.error(f"[FETCH] {err}")
        return None, err
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        logger.info(f"[FETCH] OK {url} ({len(resp.text)} chars)")
        return resp.text, None
    except Exception as e:
        err = f"Fetch failed for {url}: {type(e).__name__}"
        logger.error(f"[FETCH] {err}")
        return None, err


def source_name_from_url(url: str) -> str:
    """Extract a human-readable source name from a URL."""
    host = urlparse(url).netloc
    host = host.replace("www.", "").replace("finance.", "")
    return host.split(".")[0].title()


def extract_numerical_data(text: str) -> list[dict]:
    """Extract all numbers/percentages with surrounding context. Deterministic."""
    results = []
    for m in NUMERICAL_PATTERN.finditer(text):
        start = max(0, m.start() - 25)
        end = min(len(text), m.end() + 25)
        span = text[start:end].strip()
        label_raw = text[start:m.start()].strip().rstrip(":- ")
        results.append({
            "label": label_raw[-60:] if label_raw else "",
            "value": m.group("value").strip(),
            "unit": m.group("unit"),
            "source_span": span,
        })
    return results


def extract_content_items(html: str, source_url: str, source_name: str) -> list[dict]:
    """
    Parse HTML into structured content records. Deterministic — no LLM.
    Returns a list of raw dicts (validated by Pydantic later in normalise stage).
    """
    soup = BeautifulSoup(html, "lxml")

    # Remove scripts, styles, nav noise
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()

    now = datetime.datetime.utcnow().isoformat() + "Z"
    items = []

    # Headlines from h1/h2/h3
    for tag in soup.find_all(["h1", "h2", "h3"]):
        text = tag.get_text(separator=" ", strip=True)
        if len(text) < 10 or len(text) > 500:
            continue
        items.append({
            "content_id": f"CNT_{uuid.uuid4().hex[:8]}",
            "source_url": source_url,
            "source_name": source_name,
            "content_type": "headline",
            "title": text,
            "body": "",
            "published_at": None,
            "extracted_at": now,
            "numerical_data": extract_numerical_data(text),
        })

    # Article paragraphs
    seen_bodies: set[str] = set()
    for tag in soup.find_all("p"):
        text = tag.get_text(separator=" ", strip=True)
        if len(text) < 50 or len(text) > 5000:
            continue
        normalized = " ".join(text.split())
        if normalized in seen_bodies:
            continue
        seen_bodies.add(normalized)
        items.append({
            "content_id": f"CNT_{uuid.uuid4().hex[:8]}",
            "source_url": source_url,
            "source_name": source_name,
            "content_type": "article",
            "title": "",
            "body": text,
            "published_at": None,
            "extracted_at": now,
            "numerical_data": extract_numerical_data(text),
        })

    logger.info(f"[EXTRACT] {len(items)} items from {source_url}")
    return items


def compute_content_hash(item: dict) -> str:
    """SHA-256 of title+body for deduplication. Deterministic."""
    content = (item.get("title", "") + item.get("body", "")).strip().lower()
    content = " ".join(content.split())
    return hashlib.sha256(content.encode()).hexdigest()
