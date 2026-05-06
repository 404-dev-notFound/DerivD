# Financial Content Intelligence Pipeline

A replayable, staged pipeline that ingests public financial web sources, extracts and resolves financial entities, scores per-entity sentiment, detects cross-source conflicts, and generates multi-audience intelligence briefings — all auditable and re-runnable from a clean state.

---

## Quick Start

### Local (Python)

```bash
# 1. Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env             # then set OPENROUTER_API_KEY and LLM_MODEL

# 4. Run the pipeline (CLI)
python run_pipeline.py

# 5. Validate output
python validate.py

# 6. Or run via API server
uvicorn api:app --reload --port 8000
```

### Docker

```bash
# Build and start API server
docker compose up --build

# Or run the pipeline directly (one-shot)
docker compose run --rm pipeline
```

---

## Environment Variables

Create a `.env` file at the project root (see `.env.example`):

| Variable | Required | Description |
|---|---|---|
| `OPENROUTER_API_KEY` | Yes | API key from [openrouter.ai](https://openrouter.ai) |
| `LLM_MODEL` | Yes | Model ID, e.g. `anthropic/claude-sonnet-4-5` |
| `MAX_TOKENS` | No | Max tokens per LLM call (default: 4096) |

---

## Pipeline Stages

The pipeline enforces strict stage ordering. Skipping a stage raises `AssertionError`.

```
INIT
 → SOURCES_LOADED          Load and validate sources.json
 → CONTENT_FETCHED         Fetch HTML from each URL (deterministic)
 → CONTENT_EXTRACTED       Parse HTML → structured content (deterministic)
 → CONTENT_NORMALISED      SHA-256 dedup + Pydantic validation (deterministic)
 → ENTITIES_EXTRACTED      LLM: identify financial entity mentions
 → ENTITIES_RESOLVED       LLM: resolve aliases → canonical entity registry
 → ENTITY_SENTIMENT_SCORED LLM: per-entity sentiment with evidence spans
 → QA_AND_CONFLICTS_CHECKED LLM + code: flag conflicts, low-confidence entities
 → REPORTS_GENERATED       LLM: trader brief, analyst report, executive summary
 → COST_REPORT_GENERATED   Token usage + cost estimation (deterministic)
 → RESULTS_FINALISED       run_metrics.json + sentiment_timeline.json
```

Stages 1–4 and 10–11 are fully deterministic (no LLM). Stages 5–9 use LLM via OpenRouter.

---

## Project Structure

```
project-root/
├── run_pipeline.py              Entry point — orchestrates all stages
├── api.py                       FastAPI REST API
├── validate.py                  Evaluator validation script
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── sources.json                 Input: list of financial source URLs
├── hallucination.md             Anti-hallucination design doc
│
├── Stages/
│   ├── SOURCES_LOADED.py
│   ├── CONTENT_FETCHED.py
│   ├── CONTENT_EXTRACTED.py
│   ├── CONTENT_NORMALISED.py
│   ├── ENTITIES_EXTRACTED.py
│   ├── ENTITIES_RESOLVED.py
│   ├── ENTITY_SENTIMENT_SCORED.py
│   ├── QA_AND_CONFLICTS_CHECKED.py
│   ├── REPORTS_GENERATED.py
│   ├── COST_REPORT_GENERATED.py
│   └── RESULTS_FINALISED.py
│
├── utils/
│   ├── models.py                Pydantic v2 models for all artifact schemas
│   ├── llm_client.py            OpenRouter HTTP client + llm_calls.jsonl logging
│   └── content_utils.py         Deterministic fetch, HTML parsing, SHA-256 hashing
│
├── extracted_content.json       Generated: normalised content from all sources
├── entities.json                Generated: resolved financial entity registry
├── entity_sentiment.json        Generated: per-entity sentiment with evidence
├── qa_report.json               Generated: QA issues and conflict flags
├── cost_report.json             Generated: token usage and cost estimates
├── run_metrics.json             Generated: pipeline observability metrics
├── sentiment_timeline.json      Generated: temporal sentiment shifts
├── llm_calls.jsonl              Generated: one record per LLM call
└── reports/
    ├── trader_brief.md
    ├── analyst_report.md
    └── executive_summary.md
```

---

## REST API

Start the server:
```bash
uvicorn api:app --reload --port 8000
# Interactive docs: http://localhost:8000/docs
```

### Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | API info |
| `POST` | `/pipeline/run` | Start pipeline in background (202 Accepted) |
| `GET` | `/pipeline/status` | Current stage, status, counts, cost |
| `GET` | `/pipeline/logs?lines=200` | Tail `pipeline.log` |
| `GET` | `/artifacts/content` | `extracted_content.json` |
| `GET` | `/artifacts/entities` | `entities.json` |
| `GET` | `/artifacts/sentiment` | `entity_sentiment.json` |
| `GET` | `/artifacts/qa` | `qa_report.json` |
| `GET` | `/artifacts/cost` | `cost_report.json` |
| `GET` | `/artifacts/metrics` | `run_metrics.json` |
| `GET` | `/artifacts/timeline` | `sentiment_timeline.json` |
| `GET` | `/reports` | List available reports |
| `GET` | `/reports/trader_brief.md` | Trader Brief (Markdown) |
| `GET` | `/reports/analyst_report.md` | Analyst Report (Markdown) |
| `GET` | `/reports/executive_summary.md` | Executive Summary (Markdown) |
| `GET` | `/llm-calls?limit=50&stage=entity_extraction` | Paginated LLM call log |

### Example: Run pipeline and poll status

```bash
# Start pipeline
curl -X POST http://localhost:8000/pipeline/run

# Poll status
curl http://localhost:8000/pipeline/status

# Fetch entities after completion
curl http://localhost:8000/artifacts/entities | python -m json.tool
```

---

## Validation

```bash
python validate.py
```

Checks:
- Required artifacts exist and are valid JSON
- At least 2–3 sources were processed
- All content items have source attribution
- Numerical data preserves source spans
- Entities have aliases and source mentions
- Sentiment is entity-specific (not page-level) with evidence spans
- Low-confidence entities are flagged in `qa_report.json`
- LLM call log has entries for extraction, resolution, and sentiment stages

---

## Hallucination Prevention

All LLM outputs are validated by code before writing to disk:

1. **Content-ID guard** — every entity mention must reference a `content_id` that was actually fetched. Unknown IDs are dropped.
2. **Source-span overlap check** — evidence spans are verified against the source corpus using 50% word-overlap threshold. Unverifiable spans are dropped with a warning log.
3. **Pydantic schema enforcement** — invalid field types, out-of-range scores, and unknown enum values raise `ValidationError` and skip the offending record.
4. **Safe fallback on LLM failure** — all LLM calls are wrapped in `try/except`. Failures return empty safe values; the pipeline never crashes.

See [hallucination.md](hallucination.md) for full design documentation.

---

## Cost Tracking

Every LLM call is logged to `llm_calls.jsonl` with token usage. After the pipeline completes, `cost_report.json` shows:

- Total tokens and estimated USD cost
- Per-stage breakdown
- Efficiency strategy description (content deduplication, batching)

Pricing uses OpenRouter/Anthropic published rates for the configured model.

---

## Supported Sources (Default)

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

Replace `sources.json` with any equivalent financial content sources. The pipeline is source-agnostic.

---

## Replayability

To re-run from a clean state:

```bash
# Delete all generated artifacts
rm -f extracted_content.json entities.json entity_sentiment.json \
      qa_report.json cost_report.json run_metrics.json \
      sentiment_timeline.json llm_calls.jsonl pipeline.log
rm -rf reports/

# Re-run
python run_pipeline.py
python validate.py
```

Static precomputed outputs are not sufficient — the pipeline must regenerate all artifacts from `sources.json`.

---

## Disclaimer

This is market intelligence tooling only and must not be presented as financial advice.
