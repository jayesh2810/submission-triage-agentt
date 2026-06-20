from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, TypedDict
from uuid import uuid4

from pydantic import BaseModel, Field


SubmissionStatus = Literal[
    "running",
    "needs_review",
    "complete",
    "error",
]

QueueName = Literal[
    "ready_for_uw",
    "missing_info",
    "senior_uw_review",
    "prohibited_class_review",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:10]}"


class UploadedDocument(BaseModel):
    filename: str
    mime_type: str
    size: int
    text: str | None = None
    raw_bytes_b64: str | None = None


class BrokerMetadata(BaseModel):
    broker_name: str | None = None
    broker_email: str | None = None
    subject: str | None = None
    notes: str | None = None


class FieldConfidence(BaseModel):
    field: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: str | None = None


class PriorLoss(BaseModel):
    date: str | None = None
    description: str
    amount: float | None = None


class Location(BaseModel):
    address: str | None = None
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None


class SubmissionRecord(BaseModel):
    named_insured: str | None = None
    broker_name: str | None = None
    submission_type: Literal["new_business", "renewal", "endorsement", "unknown"] = "unknown"
    line_of_business: Literal["general_liability", "property", "bop", "workers_comp", "unknown"] = "unknown"
    business_description: str | None = None
    class_code: str | None = None
    sic_code: str | None = None
    naics_code: str | None = None
    requested_limits: dict[str, float] = Field(default_factory=dict)
    locations: list[Location] = Field(default_factory=list)
    payroll: float | None = None
    revenue: float | None = None
    tiv: float | None = None
    prior_losses: list[PriorLoss] = Field(default_factory=list)
    missing_required_fields: list[str] = Field(default_factory=list)
    field_confidence: list[FieldConfidence] = Field(default_factory=list)
    source_evidence_flags: list[str] = Field(default_factory=list)
    extraction_notes: str | None = None


class EnrichmentResult(BaseModel):
    business_profile: dict[str, Any]
    claims_signal: dict[str, Any]
    geo_cat_context: dict[str, Any]
    notes: list[str] = Field(default_factory=list)


class GuidelineResult(BaseModel):
    class_code_status: Literal["acceptable", "senior_review", "prohibited", "unknown"]
    limits_status: Literal["standard", "senior_review", "unknown"]
    prohibited_class: bool
    missing_fields: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class RoutingResult(BaseModel):
    queue: QueueName
    priority: Literal["low", "normal", "high"]
    rationale: str
    reviewer_notes: list[str] = Field(default_factory=list)


class FailureAnalysis(BaseModel):
    has_control_gap: bool = False
    agent_believed: str | None = None
    source_evidence_suggested: str | None = None
    control_gap: str | None = None
    expected_route: str | None = None


class HandoffPackage(BaseModel):
    submission_id: str
    thread_id: str
    generated_at: str
    submission: SubmissionRecord
    enrichment: EnrichmentResult | None
    guidelines: GuidelineResult | None
    routing: RoutingResult | None
    failure_analysis: FailureAnalysis
    audit_events: list[str] = Field(default_factory=list)


class TriageState(TypedDict, total=False):
    submission_id: str
    thread_id: str
    created_at: str
    status: SubmissionStatus
    broker_metadata: dict[str, Any]
    documents: list[dict[str, Any]]
    submission: dict[str, Any]
    enrichment: dict[str, Any]
    guidelines: dict[str, Any]
    routing: dict[str, Any]
    handoff: dict[str, Any]
    failure_analysis: dict[str, Any]
    audit_events: list[str]
    review_completed: bool
    review_payload: dict[str, Any]
    error: str

