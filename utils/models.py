from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field, field_validator


class NumericalData(BaseModel):
    label: str
    value: str
    unit: Optional[str] = None
    source_span: str


class ContentItem(BaseModel):
    content_id: str
    source_url: str
    source_name: str
    content_type: str  # headline | article | market_data | press_release | other
    title: str
    body: str
    published_at: Optional[str] = None
    extracted_at: str
    numerical_data: list[NumericalData] = Field(default_factory=list)
    content_hash: Optional[str] = None  # SHA-256 for dedup; stripped before final output


class SourceMention(BaseModel):
    content_id: str
    source_url: str
    mention_text: str
    source_span: str


class Entity(BaseModel):
    entity_id: str
    canonical_name: str
    entity_type: str  # currency | currency_pair | index | commodity | central_bank | economic_indicator | company | person | country | event | other
    aliases: list[str] = Field(default_factory=list)
    source_mentions: list[SourceMention] = Field(default_factory=list)
    resolution_confidence: float = Field(ge=0.0, le=1.0)


class SentimentEvidence(BaseModel):
    content_id: str
    source_url: str
    source_span: str
    reason: str


VALID_SENTIMENTS = {"bullish", "bearish", "neutral", "mixed"}
VALID_SEVERITIES = {"critical", "warning", "info"}
VALID_ISSUE_TYPES = {
    "conflicting_sentiment", "unresolved_entity", "numerical_conflict",
    "ungrounded_claim", "duplicate_content", "other"
}


class EntitySentiment(BaseModel):
    entity_id: str
    canonical_name: str
    sentiment: str
    sentiment_score: float = Field(ge=-1.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[SentimentEvidence] = Field(default_factory=list)

    @field_validator("sentiment")
    @classmethod
    def validate_sentiment(cls, v: str) -> str:
        if v not in VALID_SENTIMENTS:
            raise ValueError(f"sentiment must be one of {VALID_SENTIMENTS}")
        return v


class QAIssue(BaseModel):
    issue_id: str
    severity: str
    issue_type: str
    entities: list[str] = Field(default_factory=list)
    source_content_ids: list[str] = Field(default_factory=list)
    details: str

    @field_validator("severity")
    @classmethod
    def validate_severity(cls, v: str) -> str:
        if v not in VALID_SEVERITIES:
            raise ValueError(f"severity must be one of {VALID_SEVERITIES}")
        return v


class TimelineItem(BaseModel):
    entity_id: str
    timestamp: str
    sentiment: str
    source_content_ids: list[str] = Field(default_factory=list)
    summary: str


class RunMetrics(BaseModel):
    total_sources: int
    sources_fetched_ok: int
    sources_failed: int
    total_content_items: int
    content_items_after_dedup: int
    total_entities: int
    low_confidence_entities: int
    sentiment_records: int
    qa_issues: int
    llm_call_count: int
    pipeline_duration_seconds: float
    error_log: list[str] = Field(default_factory=list)
