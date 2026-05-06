#!/usr/bin/env python3
"""
validate.py — Evaluator validation script for the financial content intelligence pipeline.

Run: python validate.py
Exits 0 on pass, 1 on failure.

All artifacts are expected in the Artifacts/ directory (configured via utils/paths.py).
"""
from __future__ import annotations

import json
import os
import sys

# Windows legacy consoles can't print non-ASCII by default; force UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except (AttributeError, ValueError):
    pass

# Import centralised path constants so validation always looks in the right place.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils.paths import (
    EXTRACTED_CONTENT,
    ENTITIES,
    ENTITY_SENTIMENT,
    QA_REPORT,
    COST_REPORT,
    LLM_CALLS,
    REPORTS_DIR,
    SOURCES,
)

errors: list[str] = []
warnings: list[str] = []


def check(condition: bool, message: str, is_warning: bool = False) -> None:
    if not condition:
        if is_warning:
            warnings.append(message)
            print(f"  WARN  {message}")
        else:
            errors.append(message)
            print(f"  FAIL  {message}")
    else:
        print(f"  OK    {message}")


def load_json(path: str) -> list | dict | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        errors.append(f"Invalid JSON in {path}: {e}")
        print(f"  FAIL  Invalid JSON: {path}: {e}")
        return None


print("=" * 60)
print("Financial Content Intelligence Pipeline -- Validation")
print("=" * 60)

# 1. Required artifacts exist
print("\n[1] Required artifacts")
REQUIRED_FILES = [
    ("sources.json",          SOURCES),
    ("extracted_content.json", EXTRACTED_CONTENT),
    ("entities.json",          ENTITIES),
    ("entity_sentiment.json",  ENTITY_SENTIMENT),
    ("llm_calls.jsonl",        LLM_CALLS),
]
for label, path in REQUIRED_FILES:
    check(os.path.exists(path), f"File exists: {label} ({path})")

# 2. JSON validity
print("\n[2] JSON validity")
for label, path in REQUIRED_FILES:
    if label.endswith(".json") and os.path.exists(path):
        data = load_json(path)
        check(data is not None, f"Valid JSON: {label}")

# 3. Extracted content
print("\n[3] Extracted content")
content = load_json(EXTRACTED_CONTENT)
if isinstance(content, list):
    sources_seen = {item.get("source_url") for item in content if item.get("source_url")}
    check(len(sources_seen) >= 2, f"At least 2 sources processed (got {len(sources_seen)})")
    check(len(content) >= 5, f"At least 5 content items (got {len(content)})")

    bad_attribution = [c for c in content if not c.get("source_url")]
    check(len(bad_attribution) == 0,
          f"All content items have source attribution ({len(bad_attribution)} missing)")

    bad_spans = []
    for item in content:
        for nd in item.get("numerical_data", []):
            if not nd.get("source_span"):
                bad_spans.append(item.get("content_id"))
    check(len(bad_spans) == 0,
          f"All numerical data has source_span ({len(bad_spans)} missing)")
else:
    check(False, "extracted_content.json is a valid list")

# 4. Entities
print("\n[4] Entities")
entities = load_json(ENTITIES)
if isinstance(entities, list):
    check(len(entities) >= 5, f"At least 5 entities extracted (got {len(entities)})")
    for ent in entities:
        eid = ent.get("entity_id", "?")
        check("aliases" in ent, f"Entity {eid} has aliases field")
        check("source_mentions" in ent and len(ent.get("source_mentions", [])) > 0,
              f"Entity {eid} has at least 1 source mention")
        check("resolution_confidence" in ent, f"Entity {eid} has resolution_confidence")
        check("canonical_name" in ent and ent["canonical_name"],
              f"Entity {eid} has canonical_name")
else:
    check(False, "entities.json is a valid list")

# 5. Entity sentiment
print("\n[5] Entity sentiment")
sentiments = load_json(ENTITY_SENTIMENT)
if isinstance(sentiments, list):
    valid_sentiments = {"bullish", "bearish", "neutral", "mixed"}
    check(len(sentiments) >= 3, f"At least 3 sentiment records (got {len(sentiments)})")
    for s in sentiments:
        name = s.get("canonical_name", s.get("entity_id", "?"))
        check("entity_id" in s, f"Sentiment '{name}' links to entity_id")
        check(s.get("sentiment") in valid_sentiments,
              f"Sentiment '{name}' has valid value (got {s.get('sentiment')!r})")
        check("evidence" in s and len(s.get("evidence", [])) > 0,
              f"Sentiment '{name}' has evidence spans (entity-specific, not page-level)")
        for ev in s.get("evidence", []):
            check("source_span" in ev and ev["source_span"],
                  f"Sentiment '{name}' evidence has source_span")
else:
    check(False, "entity_sentiment.json is a valid list")

# 6. Low-confidence entities flagged in QA
print("\n[6] Low-confidence entities in QA report")
qa = load_json(QA_REPORT)
if isinstance(entities, list) and isinstance(qa, list):
    low_conf = [e for e in entities if e.get("resolution_confidence", 1.0) < 0.6]
    if low_conf:
        qa_entity_ids: set[str] = set()
        for issue in qa:
            qa_entity_ids.update(issue.get("entities", []))
        for ent in low_conf:
            check(ent["entity_id"] in qa_entity_ids,
                  f"Low-confidence entity {ent['entity_id']} ({ent.get('canonical_name')}) "
                  f"is flagged in qa_report")
    else:
        print("  OK    No low-confidence entities found")
elif qa is None:
    check(False, "qa_report.json exists", is_warning=True)

# 7. LLM call log
print("\n[7] LLM call log")
if os.path.exists(LLM_CALLS):
    llm_logs = []
    with open(LLM_CALLS, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    llm_logs.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    stages_present = {log.get("stage") for log in llm_logs}
    check(len(llm_logs) > 0, f"LLM call log has entries (got {len(llm_logs)})")
    check("entity_extraction" in stages_present, "LLM log has entity_extraction stage")
    check("entity_resolution" in stages_present, "LLM log has entity_resolution stage")
    check("entity_sentiment_scoring" in stages_present,
          "LLM log has entity_sentiment_scoring stage")
    bad_logs = [l for l in llm_logs
                if not l.get("timestamp") or not l.get("model") or not l.get("stage")]
    check(len(bad_logs) == 0,
          f"All LLM log entries have required fields ({len(bad_logs)} incomplete)")
else:
    check(False, "llm_calls.jsonl exists")

# 8. Optional artifacts
print("\n[8] Optional artifacts")
if isinstance(qa, list):
    valid_severities = {"critical", "warning", "info"}
    for issue in qa:
        check(issue.get("severity") in valid_severities,
              f"QA issue {issue.get('issue_id')} has valid severity", is_warning=True)
else:
    print("  INFO  qa_report.json not present (optional)")

if os.path.isdir(REPORTS_DIR):
    report_files = os.listdir(REPORTS_DIR)
    check(len(report_files) >= 1,
          f"Artifacts/reports/ has at least 1 file (got {len(report_files)})", is_warning=True)
else:
    print("  INFO  Artifacts/reports/ not present (optional stretch goal)")

if os.path.exists(COST_REPORT):
    cr = load_json(COST_REPORT)
    check(isinstance(cr, dict), "cost_report.json is a valid dict", is_warning=True)
    if isinstance(cr, dict):
        check("efficiency_strategy" in cr,
              "cost_report.json has efficiency_strategy", is_warning=True)
else:
    print("  INFO  cost_report.json not present (optional)")

# Result
print("\n" + "=" * 60)
if warnings:
    print(f"WARNINGS: {len(warnings)}")
    for w in warnings:
        print(f"  ! {w}")

if errors:
    print(f"\nVALIDATION FAILED -- {len(errors)} error(s)")
    sys.exit(1)
else:
    print("\nVALIDATION PASSED")
    sys.exit(0)
