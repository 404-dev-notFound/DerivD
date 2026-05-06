"""
api.py — FastAPI REST API for the Financial Content Intelligence Pipeline.

Endpoints
---------
POST /pipeline/run            Start pipeline in background
GET  /pipeline/status         Current stage, status, timestamps
GET  /pipeline/logs           Tail pipeline.log (last N lines)

GET  /artifacts/content       extracted_content.json
GET  /artifacts/entities      entities.json
GET  /artifacts/sentiment     entity_sentiment.json
GET  /artifacts/qa            qa_report.json
GET  /artifacts/cost          cost_report.json
GET  /artifacts/metrics       run_metrics.json
GET  /artifacts/timeline      sentiment_timeline.json

GET  /reports                 List available report files
GET  /reports/{name}          Fetch a specific report (Markdown)

GET  /llm-calls               LLM call log (paginated)

Run locally:
    uvicorn api:app --reload --port 8000
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

load_dotenv()

# ── App setup ──────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Financial Content Intelligence Pipeline",
    description="REST API for running and inspecting the financial intelligence pipeline.",
    version="1.0.0",
)

_allowed_origins = os.environ.get("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-API-Key"],
)


# ── API key auth (optional — set API_SECRET_KEY env var to enable) ────────────
_API_SECRET = os.environ.get("API_SECRET_KEY", "")


def _require_api_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    """
    Protect mutating endpoints with a shared API key.
    If API_SECRET_KEY is not set the check is skipped (dev mode — log warning).
    """
    if not _API_SECRET:
        logger.warning(
            "[AUTH] API_SECRET_KEY not set — pipeline endpoints are unprotected. "
            "Set API_SECRET_KEY env var for production."
        )
        return
    if x_api_key != _API_SECRET:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key header")

logger = logging.getLogger("api")

# ── Pipeline state (thread-safe via lock) ─────────────────────────────────────
_lock = threading.Lock()
_state: dict = {
    "status": "idle",           # idle | running | complete | failed
    "stage": "INIT",
    "started_at": None,
    "completed_at": None,
    "duration_seconds": None,
    "error": None,
    "entities_count": 0,
    "content_items_count": 0,
    "sentiment_records": 0,
    "qa_issues": 0,
    "total_cost_usd": 0.0,
}


def _set_state(**kwargs: object) -> None:
    with _lock:
        _state.update(kwargs)


def _get_state() -> dict:
    with _lock:
        return dict(_state)


# ── Pipeline worker ────────────────────────────────────────────────────────────
def _run_pipeline() -> None:
    """
    Execute all pipeline stages in order. Updates _state throughout.
    Runs in a background thread — never called on the event loop thread.
    """
    from Stages.SOURCES_LOADED import load_sources
    from Stages.CONTENT_FETCHED import fetch_content
    from Stages.CONTENT_EXTRACTED import extract_content
    from Stages.CONTENT_NORMALISED import normalise_content
    from Stages.ENTITIES_EXTRACTED import extract_entities
    from Stages.ENTITIES_RESOLVED import resolve_entities
    from Stages.ENTITY_SENTIMENT_SCORED import score_entity_sentiment
    from Stages.QA_AND_CONFLICTS_CHECKED import run_qa_and_conflicts
    from Stages.REPORTS_GENERATED import generate_reports
    from Stages.COST_REPORT_GENERATED import generate_cost_report
    from Stages.RESULTS_FINALISED import finalise

    start = time.time()
    _set_state(status="running", stage="SOURCES_LOADED", started_at=datetime.utcnow().isoformat() + "Z", error=None)
    pipeline_errors: list[str] = []

    try:
        sources = load_sources("sources.json")

        _set_state(stage="CONTENT_FETCHED")
        html_map, fetch_errors = fetch_content(sources)
        pipeline_errors.extend(fetch_errors)

        _set_state(stage="CONTENT_EXTRACTED")
        raw_items = extract_content(html_map)
        raw_count = len(raw_items)

        _set_state(stage="CONTENT_NORMALISED")
        content_items = normalise_content(raw_items)
        _set_state(content_items_count=len(content_items))

        _set_state(stage="ENTITIES_EXTRACTED")
        raw_mentions = extract_entities(content_items)

        _set_state(stage="ENTITIES_RESOLVED")
        entities = resolve_entities(raw_mentions)
        _set_state(entities_count=len(entities))

        _set_state(stage="ENTITY_SENTIMENT_SCORED")
        sentiments = score_entity_sentiment(entities, content_items)
        _set_state(sentiment_records=len(sentiments))

        _set_state(stage="QA_AND_CONFLICTS_CHECKED")
        qa_issues = run_qa_and_conflicts(entities, sentiments, content_items)
        _set_state(qa_issues=len(qa_issues))

        _set_state(stage="REPORTS_GENERATED")
        generate_reports(entities, sentiments, qa_issues, content_items)

        _set_state(stage="COST_REPORT_GENERATED")
        cost = generate_cost_report()
        _set_state(total_cost_usd=cost.get("total_cost_usd", 0.0))

        _set_state(stage="RESULTS_FINALISED")
        finalise(
            pipeline_start=start,
            sources=sources,
            fetch_errors=fetch_errors,
            raw_items_count=raw_count,
            content_items=content_items,
            entities=entities,
            sentiments=sentiments,
            qa_issues=qa_issues,
            pipeline_errors=pipeline_errors,
        )

        _set_state(
            status="complete",
            stage="RESULTS_FINALISED",
            completed_at=datetime.utcnow().isoformat() + "Z",
            duration_seconds=round(time.time() - start, 2),
        )

    except Exception as exc:
        logger.exception("Pipeline failed")
        _set_state(
            status="failed",
            error=str(exc),
            completed_at=datetime.utcnow().isoformat() + "Z",
            duration_seconds=round(time.time() - start, 2),
        )


# ── Helpers ────────────────────────────────────────────────────────────────────
ARTIFACT_MAP = {
    "content":   "extracted_content.json",
    "entities":  "entities.json",
    "sentiment": "entity_sentiment.json",
    "qa":        "qa_report.json",
    "cost":      "cost_report.json",
    "metrics":   "run_metrics.json",
    "timeline":  "sentiment_timeline.json",
}


def _load_artifact(path: str) -> list | dict:
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"Artifact not yet generated: {path}")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"Malformed artifact JSON: {e}")


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/", tags=["info"])
def root() -> dict:
    return {
        "service": "Financial Content Intelligence Pipeline",
        "version": "1.0.0",
        "docs": "/docs",
        "status_endpoint": "/pipeline/status",
    }


@app.post("/pipeline/run", status_code=202, tags=["pipeline"])
def run_pipeline(
    background_tasks: BackgroundTasks,
    _auth: None = Depends(_require_api_key),
) -> dict:
    """
    Start the pipeline. Returns 202 immediately; pipeline runs in background.
    Returns 409 if a run is already in progress.
    """
    state = _get_state()
    if state["status"] == "running":
        raise HTTPException(
            status_code=409,
            detail=f"Pipeline already running at stage: {state['stage']}",
        )

    thread = threading.Thread(target=_run_pipeline, daemon=True)
    thread.start()

    return {
        "accepted": True,
        "message": "Pipeline started in background.",
        "status_url": "/pipeline/status",
    }


@app.get("/pipeline/status", tags=["pipeline"])
def pipeline_status() -> dict:
    """Return current pipeline stage and run statistics."""
    return _get_state()


@app.get("/pipeline/logs", tags=["pipeline"])
def pipeline_logs(lines: int = Query(default=100, ge=1, le=2000)) -> PlainTextResponse:
    """Return the last N lines from pipeline.log."""
    log_path = "pipeline.log"
    if not os.path.exists(log_path):
        raise HTTPException(status_code=404, detail="pipeline.log not yet created (run pipeline first)")
    with open(log_path, encoding="utf-8") as f:
        all_lines = f.readlines()
    tail = "".join(all_lines[-lines:])
    return PlainTextResponse(content=tail, media_type="text/plain")


@app.get("/artifacts/{artifact_name}", tags=["artifacts"])
def get_artifact(artifact_name: str) -> JSONResponse:
    """
    Fetch a pipeline artifact by short name.
    Names: content, entities, sentiment, qa, cost, metrics, timeline
    """
    if artifact_name not in ARTIFACT_MAP:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown artifact '{artifact_name}'. Valid: {list(ARTIFACT_MAP)}",
        )
    data = _load_artifact(ARTIFACT_MAP[artifact_name])
    return JSONResponse(content=data)


@app.get("/reports", tags=["reports"])
def list_reports() -> dict:
    """List all generated report files."""
    reports_dir = Path("reports")
    if not reports_dir.is_dir():
        return {"reports": [], "message": "reports/ directory not yet created"}
    files = [f.name for f in reports_dir.iterdir() if f.is_file()]
    return {"reports": files}


@app.get("/reports/{name}", tags=["reports"])
def get_report(name: str) -> PlainTextResponse:
    """Fetch a report by filename (e.g. trader_brief.md)."""
    # Path traversal guard: resolve and verify the path stays inside reports/
    reports_root = Path("reports").resolve()
    path = (reports_root / name).resolve()
    if not str(path).startswith(str(reports_root)):
        raise HTTPException(status_code=400, detail="Invalid report name")
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Report '{name}' not found. Run POST /pipeline/run first.",
        )
    return PlainTextResponse(
        content=path.read_text(encoding="utf-8"),
        media_type="text/markdown",
    )


@app.get("/llm-calls", tags=["observability"])
def llm_calls(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    stage: Optional[str] = Query(default=None),
) -> dict:
    """
    Return paginated LLM call log from llm_calls.jsonl.
    Filter by stage name with ?stage=entity_extraction
    """
    log_path = "llm_calls.jsonl"
    if not os.path.exists(log_path):
        raise HTTPException(status_code=404, detail="llm_calls.jsonl not yet created")

    records: list[dict] = []
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                if stage is None or record.get("stage") == stage:
                    records.append(record)
            except json.JSONDecodeError:
                pass

    total = len(records)
    page = records[offset: offset + limit]

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "stage_filter": stage,
        "records": page,
    }
