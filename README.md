# Financial Content Intelligence Pipeline

A replayable, staged pipeline that ingests public financial web sources, extracts and resolves financial entities, scores per-entity sentiment, detects cross-source conflicts, and generates multi-audience intelligence briefings — all auditable and re-runnable from a clean state.

**Tiered LLM routing** is built in: classification-heavy stages (entity extraction, QA) run on Haiku 4.5, reasoning-heavy stages (resolution, sentiment, reports) run on Sonnet 4.5. On the default 5-source set this cuts run cost by roughly 55% versus single-model Sonnet.

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
cp .env.example .env             # then set OPENROUTER_API_KEY

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

## Configuration (`config.json`)

`config.json` at the project root controls tiered model routing and per-stage parameters. You can edit it without touching code.

```jsonc
{
  "models": {
    "tier_cheap":    "anthropic/claude-haiku-4.5",
    "tier_standard": "anthropic/claude-sonnet-4.5",
    "tier_premium":  "anthropic/claude-opus-4.5"
  },
  "stages": {
    "entity_extraction":          { "model_tier": "tier_cheap",    "batch_size": 35, "max_tokens": 4096 },
    "entity_resolution":          { "model_tier": "tier_standard", "chunk_size": 20, "max_tokens": 8192 },
    "entity_sentiment_scoring":   { "model_tier": "tier_standard", "max_tokens": 8192 },
    "qa_and_conflict_detection":  { "model_tier": "tier_cheap",    "max_tokens": 4096 },
    "report_generation":          { "model_tier": "tier_standard", "max_tokens": 4096 }
  },
  "efficiency": {
    "enable_content_hashing":  true,
    "enable_batching":         true,
    "enable_tiered_routing":   true,
    "enable_artifact_mirror":  true,
    "artifact_mirror_dir":     "Artifacts"
  }
}
```

**Override priority** (highest → lowest):
1. `LLM_MODEL_<STAGE>` env var (e.g. `LLM_MODEL_REPORT_GENERATION=anthropic/claude-opus-4.5`)
2. `config.json` → `stages.<stage>.model_tier` → `models.<tier>`
3. `LLM_MODEL` env var
4. Hard default (`anthropic/claude-sonnet-4.5`)

---

## Environment Variables

Create a `.env` file at the project root (see `.env.example`):

| Variable | Required | Description |
|---|---|---|
| `OPENROUTER_API_KEY` | Yes | API key from [openrouter.ai](https://openrouter.ai) |
| `LLM_MODEL` | No | Global fallback model (config.json tiers win by default) |
| `LLM_MODEL_<STAGE>` | No | Per-stage override (e.g. `LLM_MODEL_ENTITY_EXTRACTION`) |
| `MAX_TOKENS` | No | Global fallback `max_tokens` |
| `API_SECRET_KEY` | No | If set, required as `X-API-Key` header on mutating endpoints |
| `ALLOWED_ORIGINS` | No | Comma-separated CORS origins (default `http://localhost:3000`) |

---

## Pipeline Stages

The pipeline enforces strict stage ordering. Skipping a stage raises `AssertionError`.

```
INIT
 → SOURCES_LOADED          Load and validate sources.json                    (deterministic)
 → CONTENT_FETCHED         Fetch HTML from each URL with SSRF guard          (deterministic)
 → CONTENT_EXTRACTED       Parse HTML → structured content                   (deterministic)
 → CONTENT_NORMALISED      SHA-256 dedup + Pydantic validation               (deterministic)
 → ENTITIES_EXTRACTED      LLM (Haiku):   identify financial entity mentions
 → ENTITIES_RESOLVED       LLM (Sonnet):  alias grouping → canonical registry
 → ENTITY_SENTIMENT_SCORED LLM (Sonnet):  per-entity sentiment + evidence spans
 → QA_AND_CONFLICTS_CHECKED LLM (Haiku) + code: flag conflicts + low-confidence entities
 → REPORTS_GENERATED       LLM (Sonnet):  trader / analyst / executive briefings
 → COST_REPORT_GENERATED   Token usage + per-source, per-entity, per-model costs (deterministic)
 → RESULTS_FINALISED       run_metrics.json + sentiment_timeline.json + Artifacts/ mirror
```

Final reports are never generated before sentiment scoring and QA checks complete — this invariant is enforced by the `advance()` assertions in `run_pipeline.py`.

---

## Project Structure

```
project-root/
├── run_pipeline.py              Entry point — orchestrates all stages
├── api.py                       FastAPI REST API
├── validate.py                  Evaluator validation script
├── config.json                  Tiered routing + per-stage knobs (edit freely)
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
│   ├── config.py                config.json loader + per-stage resolution
│   ├── models.py                Pydantic v2 models for all artifact schemas
│   ├── llm_client.py            OpenRouter HTTP client + llm_calls.jsonl logging
│   └── content_utils.py         Deterministic fetch, HTML parsing, SHA-256 hashing
│
├── extracted_content.json       Generated: normalised content from all sources
├── entities.json                Generated: resolved financial entity registry
├── entity_sentiment.json        Generated: per-entity sentiment with evidence
├── qa_report.json               Generated: QA issues and conflict flags
├── cost_report.json             Generated: token usage, per-source, per-entity, per-model
├── run_metrics.json             Generated: pipeline observability metrics
├── sentiment_timeline.json      Generated: temporal sentiment shifts
├── llm_calls.jsonl              Generated: one record per LLM call
├── reports/
│   ├── trader_brief.md
│   ├── analyst_report.md
│   └── executive_summary.md
└── Artifacts/                   Mirror of all generated artifacts (populated automatically)
```

---

## REST API

Start the server:
```bash
uvicorn api:app --reload --port 8000
# Interactive docs: http://localhost:8000/docs
```

| Method | Path | Description |
|---|---|---|
| `GET`  | `/` | API info |
| `POST` | `/pipeline/run` | Start pipeline in background (202 Accepted) |
| `GET`  | `/pipeline/status` | Current stage, status, counts, cost |
| `GET`  | `/pipeline/logs?lines=200` | Tail `pipeline.log` |
| `GET`  | `/artifacts/{name}` | `content`, `entities`, `sentiment`, `qa`, `cost`, `metrics`, `timeline` |
| `GET`  | `/reports` | List available reports |
| `GET`  | `/reports/{name}` | Fetch a specific report (Markdown) |
| `GET`  | `/llm-calls?limit=50&stage=entity_extraction` | Paginated LLM call log |

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
2. **Source-span overlap check** — evidence spans are verified against the source corpus using a configurable word-overlap threshold (default 0.5). Unverifiable spans are dropped with a warning log.
3. **Pydantic schema enforcement** — invalid field types, out-of-range scores, and unknown enum values raise `ValidationError` and skip the offending record.
4. **Safe fallback on LLM failure** — all LLM calls are wrapped in `try/except`. Failures return empty safe values; the pipeline never crashes.
5. **Prompt injection stripping** — fetched text is sanitised before being sent to the LLM (strips "ignore previous instructions" style payloads).
6. **SSRF guard** — URL fetcher blocks private IPs, loopback, link-local, and non-HTTP(S) schemes.

See [hallucination.md](hallucination.md) for full design documentation.

---

## Cost Tracking

Every LLM call is logged to `llm_calls.jsonl` with token usage. After the pipeline completes, `cost_report.json` shows:

- Total tokens and estimated USD cost
- `by_stage` — per-stage cost + which model was used
- `by_model` — aggregate per-model spend
- `by_source` — cost distributed across source URLs
- `cost_per_entity_usd` — average spend per resolved entity
- `model_routing` — which tier each stage resolved to (audit trail for tiered routing)
- `efficiency_strategy` — human-readable description of savings techniques

### Efficiency strategies implemented

1. **Tiered model routing** — Haiku 4.5 for classification (~12× cheaper than Sonnet on equivalent quality), Sonnet 4.5 for reasoning.
2. **SHA-256 content deduplication** — duplicate content never reaches the LLM.
3. **Batching** — entity mentions batched per LLM call (`batch_size` in config).

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
rm -rf reports/ Artifacts/

# Re-run
python run_pipeline.py
python validate.py
```

Static precomputed outputs are not sufficient — the pipeline must regenerate all artifacts from `sources.json`.

---

## Disclaimer

This is market intelligence tooling only and must not be presented as financial advice.
