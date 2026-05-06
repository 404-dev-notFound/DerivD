# CLAUDE.md — Financial Content Intelligence Pipeline

This file specifies how to build a replayable, auditable financial content
intelligence pipeline that ingests public financial web sources, normalises
content into a common schema, extracts and resolves financial entities,
performs entity-specific sentiment analysis, detects cross-source conflicts,
and generates structured intelligence briefings.

---

## What kind of project this is

You are building a **replayable, staged financial intelligence pipeline** that:

- Reads source URLs from `sources.json`
- Fetches and extracts meaningful financial content from each source
- Normalises heterogeneous content into a common internal schema
- Identifies and resolves financial entities autonomously (no external glossary)
- Scores sentiment per resolved entity (not per page)
- Detects cross-source conflicts, contradictions, and anomalies
- Generates structured intelligence briefings in multiple formats
- Logs every LLM call to `llm_calls.jsonl`
- Can be fully re-run from a clean state by an evaluator

The evaluator will:
1. Delete all generated artifacts
2. Optionally replace `sources.json` with equivalent financial content sources
3. Run `python run_pipeline.py`
4. Run `python validate.py`
5. Inspect artifacts for correctness, stage separation, and auditability

Static precomputed outputs will fail. Everything must regenerate from the pipeline.

---

## Project file structure

```
project-root/
├── run_pipeline.py              # single entry point, enforces stage order
├── validate.py                  # evaluator runs this — check ALL artifacts
├── requirements.txt             # minimal deps, pin versions
├── CLAUDE.md                    # this file
├── sources.json                 # input: list of financial source URLs
│
├── stages/
│   ├── 01_load_sources.py       # load + validate sources.json
│   ├── 02_fetch_content.py      # HTTP fetch with error logging
│   ├── 03_extract_content.py    # parse HTML → structured content records
│   ├── 04_normalise_content.py  # coerce to common schema → extracted_content.json
│   ├── 05_extract_entities.py   # LLM Stage 1: identify financial entities
│   ├── 06_resolve_entities.py   # LLM Stage 2: resolve aliases, deduplicate
│   ├── 07_score_sentiment.py    # LLM Stage 3: per-entity sentiment scoring
│   ├── 08_qa_conflicts.py       # LLM Stage 4: QA checks + conflict detection
│   ├── 09_generate_reports.py   # LLM Stage 5: multi-audience intelligence briefings
│   ├── 10_cost_report.py        # token tracking + cost estimation
│   └── 11_finalise.py           # write run_metrics.json, sentiment_timeline.json
│
├── extracted_content.json       # normalised content from all sources
├── entities.json                # resolved financial entity registry
├── entity_sentiment.json        # per-entity sentiment with evidence
├── qa_report.json               # QA issues and conflict flags
├── cost_report.json             # token usage and cost estimates
├── run_metrics.json             # observability: latency, cache hits, error rates
├── sentiment_timeline.json      # temporal sentiment shifts per entity
├── reports/
│   ├── trader_brief.md          # concise, action-oriented, price/momentum focus
│   ├── analyst_report.md        # detailed, cross-source evidence + confidence
│   └── executive_summary.md     # macro trends and risk overview
└── llm_calls.jsonl              # one JSON record per LLM call
```

---

## Stage enforcement — implement this in run_pipeline.py

```python
# run_pipeline.py

STAGES = [
    "INIT",
    "SOURCES_LOADED",
    "CONTENT_FETCHED",
    "CONTENT_EXTRACTED",
    "CONTENT_NORMALISED",
    "ENTITIES_EXTRACTED",
    "ENTITIES_RESOLVED",
    "ENTITY_SENTIMENT_SCORED",
    "QA_AND_CONFLICTS_CHECKED",
    "REPORTS_GENERATED",
    "COST_REPORT_GENERATED",   # if attempted
    "RESULTS_FINALISED",
]

current_stage = "INIT"

def advance(expected_current: str, next_stage: str):
    global current_stage
    assert current_stage == expected_current, (
        f"Stage violation: expected {expected_current}, got {current_stage}"
    )
    current_stage = next_stage
    print(f"[STAGE] {current_stage}")

# Usage
advance("INIT", "SOURCES_LOADED")
load_sources()                        # read sources.json

advance("SOURCES_LOADED", "CONTENT_FETCHED")
fetch_content()                       # HTTP fetch, log failures

advance("CONTENT_FETCHED", "CONTENT_EXTRACTED")
extract_content()                     # parse HTML, pull headlines/articles/prices

advance("CONTENT_EXTRACTED", "CONTENT_NORMALISED")
normalise_content()                   # coerce to common schema → extracted_content.json

advance("CONTENT_NORMALISED", "ENTITIES_EXTRACTED")
extract_entities()                    # LLM Stage 1

advance("ENTITIES_EXTRACTED", "ENTITIES_RESOLVED")
resolve_entities()                    # LLM Stage 2

advance("ENTITIES_RESOLVED", "ENTITY_SENTIMENT_SCORED")
score_entity_sentiment()              # LLM Stage 3

advance("ENTITY_SENTIMENT_SCORED", "QA_AND_CONFLICTS_CHECKED")
run_qa_and_conflicts()                # LLM Stage 4

advance("QA_AND_CONFLICTS_CHECKED", "REPORTS_GENERATED")
generate_reports()                    # LLM Stage 5

advance("REPORTS_GENERATED", "COST_REPORT_GENERATED")
generate_cost_report()                # token usage + efficiency strategy

advance("COST_REPORT_GENERATED", "RESULTS_FINALISED")
finalise()                            # run_metrics.json, sentiment_timeline.json
print("[PIPELINE COMPLETE]")
```

**CRITICAL**: Stage order must match exactly. Final reports must not be generated
before entity sentiment scoring and QA checks complete.

---

## Input file: sources.json

```json
{
  "sources": [
    "https://finance.yahoo.com/quote/EURUSD%3DX/",
    "https://finance.yahoo.com/markets/",
    "https://www.federalreserve.gov/newsevents/pressreleases.htm",
    "https://www.imf.org/en/News",
    "https://www.reuters.com/markets/currencies/"
  ]
}
```

The evaluator may replace this with equivalent financial content sources.

---

## Artifact schemas

### extracted_content.json — one item per extracted content piece

```json
{
  "content_id": "string",
  "source_url": "string",
  "source_name": "string",
  "content_type": "headline | article | market_data | press_release | other",
  "title": "string",
  "body": "string",
  "published_at": "ISO-8601 timestamp | null",
  "extracted_at": "ISO-8601 timestamp",
  "numerical_data": [
    {
      "label": "string",
      "value": "string",
      "unit": "string | null",
      "source_span": "string"
    }
  ]
}
```

### entities.json — one item per resolved entity

```json
{
  "entity_id": "string",
  "canonical_name": "string",
  "entity_type": "currency | currency_pair | index | commodity | central_bank | economic_indicator | company | person | country | event | other",
  "aliases": ["string"],
  "source_mentions": [
    {
      "content_id": "string",
      "source_url": "string",
      "mention_text": "string",
      "source_span": "string"
    }
  ],
  "resolution_confidence": 0.0
}
```

Entities with `resolution_confidence < 0.6` must be flagged for review in `qa_report.json`.

### entity_sentiment.json — one item per entity

```json
{
  "entity_id": "string",
  "canonical_name": "string",
  "sentiment": "bullish | bearish | neutral | mixed",
  "sentiment_score": 0.0,
  "confidence": 0.0,
  "evidence": [
    {
      "content_id": "string",
      "source_url": "string",
      "source_span": "string",
      "reason": "string"
    }
  ]
}
```

Sentiment must cite source spans. Unsupported sentiment claims are not acceptable.

### qa_report.json — one item per QA issue

```json
{
  "issue_id": "string",
  "severity": "critical | warning | info",
  "issue_type": "conflicting_sentiment | unresolved_entity | numerical_conflict | ungrounded_claim | duplicate_content | other",
  "entities": ["entity_id"],
  "source_content_ids": ["content_id"],
  "details": "string"
}
```

### llm_calls.jsonl — one JSON object per LLM call

```json
{
  "stage": "string",
  "source_url": "string | null",
  "content_ids": ["string"],
  "timestamp": "ISO-8601 timestamp",
  "provider": "string",
  "model": "string",
  "prompt_hash": "string",
  "input_artifacts": ["path"],
  "output_artifact": "path",
  "estimated_input_tokens": 0,
  "estimated_output_tokens": 0
}
```

There must be separate records for entity extraction, entity resolution,
sentiment scoring, QA/conflict detection, and report generation.

---

## LLM call pattern — use for every AI call

```python
import hashlib, json, datetime
from anthropic import Anthropic

client = Anthropic()

def llm_call(
    stage: str,
    system: str,
    user_content: str,
    input_artifacts: list[str],
    output_artifact: str,
    source_url: str | None = None,
    content_ids: list[str] | None = None,
) -> str:
    """Make an LLM call, log it to llm_calls.jsonl, and return the response."""
    prompt_hash = hashlib.sha256(
        (system + user_content).encode()
    ).hexdigest()[:16]

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=system,
        messages=[{"role": "user", "content": user_content}],
    )

    result = response.content[0].text
    usage = response.usage

    log_entry = {
        "stage": stage,
        "source_url": source_url,
        "content_ids": content_ids or [],
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "provider": "anthropic",
        "model": "claude-sonnet-4-6",
        "prompt_hash": prompt_hash,
        "input_artifacts": input_artifacts,
        "output_artifact": output_artifact,
        "estimated_input_tokens": usage.input_tokens,
        "estimated_output_tokens": usage.output_tokens,
    }
    with open("llm_calls.jsonl", "a") as f:
        f.write(json.dumps(log_entry) + "\n")

    return result


def parse_json_response(text: str) -> dict | list:
    """Strip markdown fences and parse JSON from LLM response."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())
```

---

## System Prompts

### Stage 1 — Entity Extraction

```python
SYSTEM_EXTRACT_ENTITIES = """
You are a financial entity extraction engine. Extract all financial entities
from the provided normalised content.

Entity types:
- currency (USD, EUR, GBP, JPY, etc.)
- currency_pair (EUR/USD, GBP/USD, etc.)
- index (S&P 500, NASDAQ, DAX, etc.)
- commodity (Gold, Oil, Silver, etc.)
- central_bank (Federal Reserve, ECB, Bank of England, etc.)
- economic_indicator (CPI, NFP, GDP, etc.)
- company (Apple, Goldman Sachs, etc.)
- person (Jerome Powell, Christine Lagarde, etc.)
- country (United States, Germany, etc.)
- event (FOMC meeting, rate decision, etc.)
- other

For each entity found, record:
- The exact mention text as it appears in the source
- The surrounding source_span (10-15 words of context)
- The content_id where it was found
- The source_url

Output valid JSON only. No markdown fences.

Output schema:
{
  "entities": [
    {
      "mention_text": "string",
      "entity_type": "string",
      "content_id": "string",
      "source_url": "string",
      "source_span": "string"
    }
  ]
}
"""
```

### Stage 2 — Entity Resolution

```python
SYSTEM_RESOLVE_ENTITIES = """
You are a financial entity resolution engine. Given a list of raw entity
mentions, resolve aliases and produce a canonical entity registry.

Resolution examples:
- The Fed = Federal Reserve = US central bank → canonical: "Federal Reserve"
- greenback = USD = US dollar → canonical: "USD"
- EUR/USD = EURUSD = euro-dollar → canonical: "EUR/USD"
- NFP = nonfarm payrolls → canonical: "Nonfarm Payrolls"

Rules:
- Assign a stable entity_id (e.g. ENT_001)
- Choose the most specific, widely recognised canonical name
- List all alias variants seen across sources
- Set resolution_confidence between 0.0 and 1.0
- Flag entities below 0.6 confidence as needing review

Output valid JSON only. No markdown fences.

Output schema:
{
  "entities": [
    {
      "entity_id": "string",
      "canonical_name": "string",
      "entity_type": "string",
      "aliases": ["string"],
      "source_mentions": [
        {
          "content_id": "string",
          "source_url": "string",
          "mention_text": "string",
          "source_span": "string"
        }
      ],
      "resolution_confidence": 0.0
    }
  ]
}
"""
```

### Stage 3 — Per-Entity Sentiment Scoring

```python
SYSTEM_SCORE_SENTIMENT = """
You are a financial sentiment analysis engine. Score sentiment per entity,
not per page. One article may be bullish on one entity and bearish on another.

Sentiment values: bullish | bearish | neutral | mixed
sentiment_score: -1.0 (strongly bearish) to +1.0 (strongly bullish)
confidence: 0.0 to 1.0

For each entity, review all content mentioning it and determine:
1. The overall sentiment signal
2. The specific source spans that justify that signal
3. A brief reason for each evidence item

Never assert sentiment without citing source spans.

Output valid JSON only. No markdown fences.

Output schema:
{
  "entity_sentiment": [
    {
      "entity_id": "string",
      "canonical_name": "string",
      "sentiment": "bullish | bearish | neutral | mixed",
      "sentiment_score": 0.0,
      "confidence": 0.0,
      "evidence": [
        {
          "content_id": "string",
          "source_url": "string",
          "source_span": "string",
          "reason": "string"
        }
      ]
    }
  ]
}
"""
```

### Stage 4 — QA and Conflict Detection

```python
SYSTEM_QA_CONFLICTS = """
You are a financial intelligence QA engine. Review all extracted content,
entities, and sentiment scores for quality issues and conflicts.

Flag:
- conflicting_sentiment: same entity rated bullish by one source, bearish by another
- unresolved_entity: entity with resolution_confidence < 0.6
- numerical_conflict: same metric reported differently across sources
- ungrounded_claim: sentiment claim with no cited source span
- duplicate_content: near-identical content from different sources
- other: any other quality concern

Severity levels: critical | warning | info

For each issue, cite the entity_ids and content_ids involved.

Output valid JSON only. No markdown fences.

Output schema:
{
  "qa_issues": [
    {
      "issue_id": "string",
      "severity": "critical | warning | info",
      "issue_type": "string",
      "entities": ["entity_id"],
      "source_content_ids": ["content_id"],
      "details": "string"
    }
  ]
}
"""
```

### Stage 5 — Report Generation

```python
SYSTEM_TRADER_BRIEF = """
You are generating a Trader Brief from financial intelligence data.

Style: concise, action-oriented, emphasise price levels, momentum, and
directional signals. Aim for 300-500 words. Use bullet points for key signals.

Ground every claim in the entity sentiment and content data provided.
Do not hallucinate prices, figures, or market positions.
Include a disclaimer: "This is market intelligence tooling only and must
not be presented as financial advice."
"""

SYSTEM_ANALYST_REPORT = """
You are generating an Analyst Report from financial intelligence data.

Style: detailed, include cross-source evidence, confidence scores, and
conflicting signals. Aim for 800-1200 words. Cite source URLs.

Ground every claim in the entity sentiment and content data provided.
Flag where sources disagree and explain the conflict.
Include a disclaimer: "This is market intelligence tooling only and must
not be presented as financial advice."
"""

SYSTEM_EXECUTIVE_SUMMARY = """
You are generating an Executive Summary from financial intelligence data.

Style: high-level, focuses on macro trends, key risks, and strategic outlook.
Aim for 300-500 words. Use plain language suitable for non-technical readers.

Ground every claim in the entity sentiment and content data provided.
Include a disclaimer: "This is market intelligence tooling only and must
not be presented as financial advice."
"""
```

---

## Content fetching and extraction

```python
import requests
from bs4 import BeautifulSoup
import hashlib, datetime, uuid

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; FinancialIntelligenceBot/1.0)"
    )
}

def fetch_source(url: str, timeout: int = 15) -> tuple[str | None, str | None]:
    """Fetch raw HTML. Returns (html, error_message)."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        return resp.text, None
    except Exception as e:
        error = f"Fetch failed for {url}: {e}"
        print(f"[ERROR] {error}")
        return None, error


def extract_content_items(html: str, source_url: str, source_name: str) -> list:
    """
    Parse HTML and return a list of content records.
    Deterministic — BeautifulSoup only, no LLM.
    """
    soup = BeautifulSoup(html, "html.parser")
    items = []

    # Headlines
    for tag in soup.find_all(["h1", "h2", "h3"]):
        text = tag.get_text(strip=True)
        if len(text) < 10:
            continue
        items.append({
            "content_id": f"CNT_{uuid.uuid4().hex[:8]}",
            "source_url": source_url,
            "source_name": source_name,
            "content_type": "headline",
            "title": text,
            "body": "",
            "published_at": None,
            "extracted_at": datetime.datetime.utcnow().isoformat() + "Z",
            "numerical_data": extract_numerical_data(text),
        })

    # Article paragraphs
    for tag in soup.find_all("p"):
        text = tag.get_text(strip=True)
        if len(text) < 50:
            continue
        items.append({
            "content_id": f"CNT_{uuid.uuid4().hex[:8]}",
            "source_url": source_url,
            "source_name": source_name,
            "content_type": "article",
            "title": "",
            "body": text,
            "published_at": None,
            "extracted_at": datetime.datetime.utcnow().isoformat() + "Z",
            "numerical_data": extract_numerical_data(text),
        })

    return items


import re

NUMERICAL_PATTERN = re.compile(
    r'(?P<value>[-+]?\d[\d,]*\.?\d*\s*(?:%|bps|bp|pct|percent)?)'
    r'(?:\s+(?P<unit>[A-Za-z/]+))?'
)

def extract_numerical_data(text: str) -> list:
    """Extract numbers and percentages from text. Deterministic."""
    results = []
    for m in NUMERICAL_PATTERN.finditer(text):
        start = max(0, m.start() - 20)
        end = min(len(text), m.end() + 20)
        results.append({
            "label": text[start:m.start()].strip().rstrip(": "),
            "value": m.group("value").strip(),
            "unit": m.group("unit"),
            "source_span": text[start:end].strip(),
        })
    return results
```

---

## Cost tracking

```python
ANTHROPIC_PRICING = {
    "claude-sonnet-4-6": {
        "input_per_million": 3.00,
        "output_per_million": 15.00,
    },
    "claude-haiku-4-5": {
        "input_per_million": 0.25,
        "output_per_million": 1.25,
    },
}

def compute_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = ANTHROPIC_PRICING.get(model, ANTHROPIC_PRICING["claude-sonnet-4-6"])
    return (
        input_tokens / 1_000_000 * pricing["input_per_million"]
        + output_tokens / 1_000_000 * pricing["output_per_million"]
    )


def generate_cost_report(llm_log_path: str = "llm_calls.jsonl") -> dict:
    import json
    with open(llm_log_path) as f:
        calls = [json.loads(line) for line in f if line.strip()]

    total_input = sum(c.get("estimated_input_tokens", 0) for c in calls)
    total_output = sum(c.get("estimated_output_tokens", 0) for c in calls)

    by_stage = {}
    for c in calls:
        stage = c["stage"]
        if stage not in by_stage:
            by_stage[stage] = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
        by_stage[stage]["input_tokens"] += c.get("estimated_input_tokens", 0)
        by_stage[stage]["output_tokens"] += c.get("estimated_output_tokens", 0)
        by_stage[stage]["cost_usd"] += compute_cost(
            c.get("model", "claude-sonnet-4-6"),
            c.get("estimated_input_tokens", 0),
            c.get("estimated_output_tokens", 0),
        )

    return {
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_cost_usd": compute_cost("claude-sonnet-4-6", total_input, total_output),
        "by_stage": by_stage,
        "efficiency_strategy": (
            "Content deduplication via SHA-256 hash before LLM calls; "
            "batching entity mentions per source to reduce round-trips."
        ),
    }
```

---

## Efficiency strategies

Apply at least one:

- **Content hashing**: SHA-256 each content block; skip LLM if hash already processed
- **Batching**: Send all entity mentions from a source in one call, not per-mention
- **Deduplication**: Remove near-duplicate headlines before entity extraction
- **Tiered routing**: Use Haiku for lightweight extraction, Sonnet for sentiment + reports

---

## validate.py — what to check

```python
#!/usr/bin/env python3
"""Validation script for the financial content intelligence pipeline."""

import json, os, sys

errors = []

def check(condition: bool, message: str):
    if not condition:
        errors.append(message)
        print(f"  FAIL  {message}")
    else:
        print(f"  OK    {message}")


# 1. Required artifacts exist
REQUIRED = [
    "sources.json",
    "extracted_content.json",
    "entities.json",
    "entity_sentiment.json",
    "llm_calls.jsonl",
]
for path in REQUIRED:
    check(os.path.exists(path), f"File exists: {path}")

# 2. JSON files are valid
for path in REQUIRED:
    if path.endswith(".json") and os.path.exists(path):
        try:
            with open(path) as f:
                json.load(f)
            check(True, f"Valid JSON: {path}")
        except Exception as e:
            check(False, f"Invalid JSON {path}: {e}")

# 3. At least 2-3 sources processed
if os.path.exists("extracted_content.json"):
    with open("extracted_content.json") as f:
        content = json.load(f)
    sources_seen = {item["source_url"] for item in content}
    check(len(sources_seen) >= 2, f"At least 2 sources processed (got {len(sources_seen)})")

    # Source attribution present on every item
    for item in content:
        check("source_url" in item and item["source_url"],
              f"Content {item.get('content_id')} has source attribution")

    # Numerical data preserves source spans
    for item in content:
        for nd in item.get("numerical_data", []):
            check("source_span" in nd and nd["source_span"],
                  f"Numerical data in {item.get('content_id')} has source_span")

# 4. Entities have aliases and source mentions
if os.path.exists("entities.json"):
    with open("entities.json") as f:
        entities = json.load(f)
    check(len(entities) >= 5, f"At least 5 entities extracted (got {len(entities)})")
    for ent in entities:
        check("aliases" in ent,
              f"Entity {ent.get('entity_id')} has aliases field")
        check("source_mentions" in ent and len(ent["source_mentions"]) > 0,
              f"Entity {ent.get('entity_id')} has source mentions")

# 5. Sentiment is entity-specific (not page-level) and has evidence
if os.path.exists("entity_sentiment.json"):
    with open("entity_sentiment.json") as f:
        sentiments = json.load(f)
    check(len(sentiments) >= 3, f"At least 3 entity sentiment records (got {len(sentiments)})")
    for s in sentiments:
        check("evidence" in s and len(s["evidence"]) > 0,
              f"Sentiment for {s.get('canonical_name')} has evidence spans")
        check("entity_id" in s,
              f"Sentiment record links to entity_id")
        valid_sentiments = {"bullish", "bearish", "neutral", "mixed"}
        check(s.get("sentiment") in valid_sentiments,
              f"Sentiment value '{s.get('sentiment')}' is valid")

# 6. Low-confidence entities flagged (check qa_report if it exists)
if os.path.exists("entities.json") and os.path.exists("qa_report.json"):
    with open("entities.json") as f:
        entities = json.load(f)
    low_conf = [e for e in entities if e.get("resolution_confidence", 1.0) < 0.6]
    if low_conf:
        with open("qa_report.json") as f:
            qa = json.load(f)
        qa_entity_ids = {eid for issue in qa for eid in issue.get("entities", [])}
        for ent in low_conf:
            check(ent["entity_id"] in qa_entity_ids,
                  f"Low-confidence entity {ent['entity_id']} flagged in qa_report")

# 7. LLM call log has required stages
if os.path.exists("llm_calls.jsonl"):
    with open("llm_calls.jsonl") as f:
        llm_logs = [json.loads(line) for line in f if line.strip()]
    stages_present = {log["stage"] for log in llm_logs}
    check("entity_extraction" in stages_present, "Entity extraction stage logged")
    check("entity_resolution" in stages_present, "Entity resolution stage logged")
    check("entity_sentiment_scoring" in stages_present, "Sentiment scoring stage logged")

# 8. Optional: QA report
if os.path.exists("qa_report.json"):
    with open("qa_report.json") as f:
        qa = json.load(f)
    valid_severities = {"critical", "warning", "info"}
    for issue in qa:
        check(issue.get("severity") in valid_severities,
              f"QA issue {issue.get('issue_id')} has valid severity")

# 9. Optional: reports dir
if os.path.isdir("reports"):
    report_files = os.listdir("reports")
    check(len(report_files) >= 1, f"At least 1 report in reports/ (got {len(report_files)})")

# Result
print()
if errors:
    print(f"VALIDATION FAILED — {len(errors)} error(s)")
    sys.exit(1)
else:
    print("VALIDATION PASSED ✓")
    sys.exit(0)
```

---

## requirements.txt

```
anthropic>=0.25.0
requests>=2.31.0
beautifulsoup4>=4.12.0
lxml>=4.9.0
python-dateutil>=2.8.2
```

Pin versions. Add domain-specific deps as needed.

---

## Common mistakes to avoid

| Mistake | Fix |
|---|---|
| Generating reports before sentiment scoring | Enforce stage order; assert stage state |
| Page-level sentiment only | Score per resolved entity across all mentions |
| Sentiment without cited spans | Every evidence item must include source_span |
| Silently dropping failed sources | Log fetch failures; continue with remaining sources |
| Hallucinating prices or figures | Extract only from fetched HTML; cite source_span |
| Single LLM call for extract+resolve+sentiment | Three separate calls, three log entries |
| Missing entity aliases | Resolve all known aliases (Fed = Federal Reserve, etc.) |
| Conflicting signals hidden | Flag contradictions in qa_report.json |
| Static precomputed artifacts | Delete and regenerate from pipeline end-to-end |
| No token tracking | Log usage from every response.usage |

---

## Checklist before submitting

- [ ] `python run_pipeline.py` completes without errors from a clean state
- [ ] `python validate.py` exits 0
- [ ] `sources.json` present and readable
- [ ] `extracted_content.json` covers at least 2-3 sources with attribution
- [ ] `entities.json` has canonical names, aliases, and source mentions
- [ ] `entity_sentiment.json` is entity-specific with evidence spans
- [ ] `llm_calls.jsonl` has separate records for extraction, resolution, and sentiment
- [ ] Low-confidence entities flagged in `qa_report.json`
- [ ] `cost_report.json` includes efficiency strategy explanation
- [ ] `reports/` contains at least a Trader Brief and Analyst Report
- [ ] No sentiment claim lacks a source span citation
- [ ] Pipeline enforces stage order (advance() assertions)
- [ ] Fetch failures are logged, not silently swallowed
- [ ] Reports include disclaimer that this is not financial advice
