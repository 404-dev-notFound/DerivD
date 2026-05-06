## BUILD

Build a replayable financial content intelligence pipeline that ingests content from multiple public financial web sources, normalises heterogeneous content into a common schema, extracts and resolves financial entities, performs entity-specific sentiment analysis, detects cross-source conflicts, and generates structured intelligence briefings.

This is not a one-shot market summary task. The evaluator will run your pipeline from a clean checkout, may replace the source list with equivalent fixtures, and will verify that extraction, entity resolution, sentiment scoring, QA, and reporting are staged and auditable.

The pipeline must preserve source attribution, timestamps, numerical data, evidence spans, and confidence scores throughout.

---

## INPUT FILES

Your pipeline must read source configuration from disk:

- `sources.json`

The sample source set below is provided for local testing.

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

The evaluator may replace the source list with equivalent financial content sources.

---

## PIPELINE STAGES

Your implementation must enforce these stages in code:

```text
INIT
 -> SOURCES_LOADED
 -> CONTENT_FETCHED
 -> CONTENT_EXTRACTED
 -> CONTENT_NORMALISED
 -> ENTITIES_EXTRACTED
 -> ENTITIES_RESOLVED
 -> ENTITY_SENTIMENT_SCORED
 -> QA_AND_CONFLICTS_CHECKED
 -> REPORTS_GENERATED
 -> COST_REPORT_GENERATED, if attempted
 -> RESULTS_FINALISED
```

Final reports must not be generated before entity-specific sentiment scoring and QA checks have completed.

---

## MUST COMPLETE

### 1. Content Extraction

Extract meaningful financial content from at least 2-3 configured sources.

Extract content such as:

- headlines
- article bodies
- dates
- figures
- market prices
- percentage moves
- central bank statements
- macroeconomic references
- named people or institutions

Normalise all extracted content into a common internal representation.

Save output to `extracted_content.json`.

Each item must include:

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

Do not silently drop failed sources. Log fetch or extraction failures.

---

### 2. Autonomous Entity Recognition and Resolution

Identify financial entities without relying on a provided external glossary.

Entities may include:

- currency pairs
- currencies
- indices
- commodities
- central banks
- economic indicators
- companies
- people
- countries or regions
- policy events

Resolve aliases across sources.

Examples:

```text
The Fed = Federal Reserve = US central bank
greenback = USD = US dollar
EUR/USD = EURUSD = euro-dollar
NFP = nonfarm payrolls
```

Save output to `entities.json`.

Each entity must include:

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

Entities with low confidence must be flagged for review.

---

### 3. Per-Entity Sentiment Scoring

Perform sentiment analysis per resolved entity, not per page.

One article may be bullish on one entity and bearish on another.

Save output to `entity_sentiment.json`.

Each sentiment record must include:

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

Sentiment must cite source spans that justify the signal.

Unsupported sentiment claims are not acceptable.

---

## SHOULD ATTEMPT

### 4. Cost Tracking and Efficiency

Log token usage or estimated token usage per AI call.

Calculate:

- estimated cost per source
- estimated cost per entity
- estimated total run cost

Save output to `cost_report.json`.

Apply at least one efficiency strategy:

- deduplication of overlapping content
- batching
- caching
- content hashing
- tiered model routing

Explain the strategy in the cost report.

---

### 5. Basic QA and Conflict Detection

Generate `qa_report.json`.

Flag:

- contradictory sentiment for the same entity across sources
- entities that could not be confidently resolved
- numerical data that appears inconsistent across sources
- source claims that are not traceable to extracted content
- duplicate or near-duplicate content

Each QA issue must include:

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

---

## STRETCH

### 6. Scale Up

Extend the pipeline to all 5 configured sources and track 20 or more distinct entities.

---

### 7. Multi-Audience Report Generation

Generate at least 2 report variants from the same underlying entity and sentiment data.

Supported report types:

- Trader Brief: concise, action-oriented, emphasises price levels and momentum
- Analyst Report: detailed, includes cross-source evidence and confidence
- Executive Summary: high-level, focuses on macro trends and risk

Save reports under:

```text
reports/
```

---

### 8. Full QA and Observability

Add:

- source reliability weighting
- hallucination detection
- entity resolution confidence distribution
- extraction error rate
- latency per source
- cache hit rate

Save output to `run_metrics.json`.

---

### 9. Temporal Intelligence

If dated content is available, detect sentiment shifts over time.

Save output to `sentiment_timeline.json`.

Each timeline item must include:

```json
{
  "entity_id": "string",
  "timestamp": "ISO-8601 timestamp",
  "sentiment": "bullish | bearish | neutral | mixed",
  "source_content_ids": ["content_id"],
  "summary": "string"
}
```

---

## REQUIRED ARTIFACTS

Your repository must produce:

- `sources.json`
- `extracted_content.json`
- `entities.json`
- `entity_sentiment.json`
- `qa_report.json`, if attempted
- `cost_report.json`, if attempted
- `reports/`, if attempted
- `run_metrics.json`, if attempted
- `sentiment_timeline.json`, if attempted
- `llm_calls.jsonl`

---

## `llm_calls.jsonl` REQUIREMENTS

Log one JSON object per AI call.

Each record must include:

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

There must be separate records for:

- entity extraction or resolution
- sentiment scoring
- QA or conflict detection, if AI-assisted
- report generation, if AI-assisted

---

## VALIDATION REQUIREMENTS

The repository must include a validation command, for example:

```bash
make validate
```

or:

```bash
python validate.py
```

The validation command must check that:

- required artifacts exist
- JSON files are valid
- at least 2-3 sources were processed
- extracted content includes source attribution
- numerical data preserves source spans
- entities include aliases and source mentions
- entity sentiment is not only page-level sentiment
- sentiment records include evidence spans
- unresolved or low-confidence entities are flagged
- QA report is generated if attempted
- LLM call logs or AI call logs exist

---

## EXECUTION REQUIREMENTS

The evaluator will run the pipeline from a clean checkout.

Generated artifacts may be deleted before evaluation.

The evaluator may replace `sources.json` with equivalent financial sources.

Static precomputed outputs are not sufficient.

The solution must actually run the staged pipeline and regenerate required artifacts.

---

## TOOLS

Any programming language may be used.

Python, TypeScript, or JavaScript are preferred.

Any LLM provider or AI tooling may be used.

Any scraping, parsing, or HTML-processing libraries may be used if dependencies are documented.

---

## TECHNICAL CONSTRAINTS

- Preserve source attribution for every extracted item.
- Preserve timestamps where available.
- Preserve numerical data and source spans.
- Entity sentiment must be entity-specific.
- Do not provide only page-level sentiment.
- Do not hallucinate prices, figures, or claims.
- Conflicting signals should be flagged rather than hidden.
- Reports must be grounded in extracted content.
- This is market intelligence tooling only and must not be presented as financial advice.
- Static precomputed outputs are not sufficient.