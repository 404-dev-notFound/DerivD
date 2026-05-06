# Hallucination Prevention Skill

This skill defines how the pipeline prevents LLM hallucination at every stage.
It is implemented at **two levels**: prompt-level constraints and code-level enforcement.

---

## Core Principle

The LLM is only allowed to *reason about* data that is explicitly provided in the prompt.
It must never invent, infer, or estimate information beyond what is in the source corpus.

---

## Code-Enforced Guards (Deterministic)

These checks run in Python *after* every LLM call, regardless of what the model says:

### 1. Content-ID Validation
Every entity mention and sentiment evidence record must reference a `content_id`
that exists in the actual extracted content. References to unknown IDs are silently
dropped by the code before writing to disk.

```python
# In ENTITIES_EXTRACTED.py
valid = [m for m in mentions if m.get("content_id") in batch_ids]
```

### 2. Source-Span Overlap Check
Every `source_span` field is checked against the corpus text using word-overlap:
- Tokenise the span into words longer than 3 characters
- Require ≥50% of those words to appear in the source corpus text
- Spans failing this threshold are dropped with a warning log

```python
# In utils/llm_client.py: validate_spans_against_corpus()
matches = sum(1 for w in words if w in corpus_lower)
overlap = matches / len(words)
if overlap < 0.5:
    logger.warning(f"Rejected unverifiable span: {span[:60]}")
```

### 3. Entity-ID Validation in Sentiment
Sentiment records referencing unknown `entity_id` values are dropped before writing
`entity_sentiment.json`. The model cannot invent new entities at the sentiment stage.

### 4. Pydantic Schema Enforcement
Every LLM output is parsed through strict Pydantic models:
- Unknown fields are ignored
- Out-of-range scores (sentiment_score outside [-1,1]) raise ValidationError
- Invalid enum values (e.g. `"very_bullish"`) raise ValidationError
- Failed records are logged and skipped; the pipeline never crashes on bad LLM output

### 5. Retry with Safe Fallback
If an LLM call fails (network error, malformed JSON, all retries exhausted):
- Log the error with full context
- Return an empty safe value (`[]` or `{}`)
- Pipeline continues to next stage
- Empty values are reflected in QA report and run_metrics.json

---

## Prompt-Level Constraints (In Every System Prompt)

Each stage system prompt includes these explicit anti-hallucination instructions:

```
STRICT ANTI-HALLUCINATION RULES:
1. Only extract/cite data EXPLICITLY present in the content provided.
2. Do NOT invent, infer, or assume any information not in the text.
3. source_span MUST be a verbatim excerpt from the provided content.
4. content_id MUST exactly match one of the provided content IDs.
5. If insufficient data exists, return an empty array — do not fabricate.
```

---

## Stage-by-Stage Responsibilities

| Stage | LLM Allowed To Do | LLM NOT Allowed To Do |
|---|---|---|
| ENTITIES_EXTRACTED | Classify entity types, identify spans | Invent entities not in text |
| ENTITIES_RESOLVED | Group aliases, assign confidence | Reference content_ids not provided |
| ENTITY_SENTIMENT_SCORED | Reason about sentiment from spans | Invent prices or figures |
| QA_AND_CONFLICTS_CHECKED | Flag contradictions in provided data | Invent conflicts not in data |
| REPORTS_GENERATED | Synthesise from structured data | Introduce market data not in source |

---

## What is Explicitly NOT the Model's Job

These are handled entirely by deterministic code:

- Deduplication (SHA-256 hashing)
- Content-ID tracking
- Schema validation (Pydantic)
- Cost/token tracking
- Stage ordering (advance() assertions)
- Low-confidence entity flagging (threshold check)
- Source attribution (always set by fetcher, never by LLM)
- Error recovery (Python try/except, not model retry)

---

## Testing Hallucination Resistance

To verify the guards work:

1. Run the pipeline on real sources
2. Inspect `llm_calls.jsonl` for `"error": null` entries
3. Check that all `source_span` values in `entity_sentiment.json` appear in `extracted_content.json`
4. Check that all `entity_id` values in `entity_sentiment.json` appear in `entities.json`
5. Run `python validate.py` — it will fail if unsupported claims appear

```bash
python run_pipeline.py
python validate.py
```

---

## Logging

All hallucination guard rejections are logged at WARNING level with the prefix
`[HALLUCINATION GUARD]` so they can be audited in `pipeline.log`:

```
WARNING [HALLUCINATION GUARD] Rejected span with 20% overlap: 'Fed raised rates to 6%...'
WARNING [ENTITIES_EXTRACTED] Dropped 2 mentions with invalid content_ids
WARNING [ENTITIES_RESOLVED] Dropped mention with invalid content_id=CNT_deadbeef
```
