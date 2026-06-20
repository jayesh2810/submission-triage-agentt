from __future__ import annotations

from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from triage.enrichment import enrich_submission
from triage.guidelines import analyze_failure, evaluate_guidelines, route_submission
from triage.llm import GeminiExtractionError, extract_submission
from triage.models import (
    BrokerMetadata,
    HandoffPackage,
    SubmissionRecord,
    TriageState,
    now_iso,
)


LOW_CONFIDENCE_THRESHOLD = 0.65


def _audit(state: TriageState, message: str) -> list[str]:
    return [*state.get("audit_events", []), f"{now_iso()} | {message}"]


def ingest_and_classify(state: TriageState) -> dict[str, Any]:
    metadata = BrokerMetadata.model_validate(state.get("broker_metadata", {}))
    docs = state.get("documents", [])
    return {
        "status": "running",
        "audit_events": _audit(
            state,
            f"Step 1 complete: ingested {len(docs)} document(s) for {metadata.broker_name or 'unknown broker'}.",
        ),
    }


def extract_and_structure(state: TriageState) -> dict[str, Any]:
    metadata = BrokerMetadata.model_validate(state.get("broker_metadata", {}))
    docs = [d for d in state.get("documents", [])]
    from triage.models import UploadedDocument

    parsed_docs = [UploadedDocument.model_validate(d) for d in docs]
    try:
        submission = extract_submission(parsed_docs, metadata)
    except GeminiExtractionError as exc:
        return {
            "status": "error",
            "error": str(exc),
            "audit_events": _audit(state, f"Step 2 failed: {exc}"),
        }
    return {
        "submission": submission.model_dump(),
        "audit_events": _audit(
            state,
            f"Step 2 complete: extracted class {submission.class_code or 'unknown'} for {submission.named_insured or 'unknown insured'}.",
        ),
    }


def after_extract(state: TriageState) -> str:
    if state.get("status") == "error" or state.get("error"):
        return "stop"
    return "continue"


def enrich(state: TriageState) -> dict[str, Any]:
    submission = SubmissionRecord.model_validate(state["submission"])
    enrichment = enrich_submission(submission)
    return {
        "enrichment": enrichment.model_dump(),
        "audit_events": _audit(state, "Step 3 complete: mock enrichment attached."),
    }


def appetite_and_eligibility(state: TriageState) -> dict[str, Any]:
    submission = SubmissionRecord.model_validate(state["submission"])
    guidelines = evaluate_guidelines(submission)
    return {
        "guidelines": guidelines.model_dump(),
        "audit_events": _audit(state, "Step 4 complete: class, limits, and prohibited-class guidelines checked."),
    }


def review_gate(state: TriageState) -> dict[str, Any]:
    if state.get("review_completed"):
        return {}

    submission = SubmissionRecord.model_validate(state["submission"])
    reasons = []
    if submission.missing_required_fields:
        reasons.append(f"Missing required fields: {', '.join(submission.missing_required_fields)}")
    low_conf = [
        f"{fc.field}={fc.confidence:.2f}"
        for fc in submission.field_confidence
        if fc.confidence < LOW_CONFIDENCE_THRESHOLD
    ]
    if low_conf:
        reasons.append(f"Low confidence fields: {', '.join(low_conf)}")

    if not reasons:
        return {"review_completed": True}

    review_payload = interrupt(
        {
            "submission_id": state["submission_id"],
            "reasons": reasons,
            "submission": submission.model_dump(),
        }
    )
    edited = review_payload.get("submission") if isinstance(review_payload, dict) else None
    updates: dict[str, Any] = {
        "review_completed": True,
        "review_payload": review_payload if isinstance(review_payload, dict) else {},
        "audit_events": _audit(state, "Manual review completed and graph resumed."),
    }
    if edited:
        updates["submission"] = SubmissionRecord.model_validate(edited).model_dump()
        updates["guidelines"] = evaluate_guidelines(SubmissionRecord.model_validate(edited)).model_dump()
    return updates


def route(state: TriageState) -> dict[str, Any]:
    from triage.models import GuidelineResult

    guidelines = GuidelineResult.model_validate(state["guidelines"])
    routing = route_submission(guidelines)
    return {
        "routing": routing.model_dump(),
        "audit_events": _audit(state, f"Step 5 complete: routed to {routing.queue}."),
    }


def handoff(state: TriageState) -> dict[str, Any]:
    from triage.models import EnrichmentResult, GuidelineResult, RoutingResult

    submission = SubmissionRecord.model_validate(state["submission"])
    enrichment = EnrichmentResult.model_validate(state["enrichment"])
    guidelines = GuidelineResult.model_validate(state["guidelines"])
    routing = RoutingResult.model_validate(state["routing"])
    failure = analyze_failure(submission, routing)
    handoff_package = HandoffPackage(
        submission_id=state["submission_id"],
        thread_id=state["thread_id"],
        generated_at=now_iso(),
        submission=submission,
        enrichment=enrichment,
        guidelines=guidelines,
        routing=routing,
        failure_analysis=failure,
        audit_events=_audit(state, "Step 6 complete: handoff package generated."),
    )
    return {
        "status": "complete",
        "failure_analysis": failure.model_dump(),
        "handoff": handoff_package.model_dump(),
        "audit_events": handoff_package.audit_events,
    }


def build_graph():
    builder = StateGraph(TriageState)
    builder.add_node("ingest_and_classify", ingest_and_classify)
    builder.add_node("extract_and_structure", extract_and_structure)
    builder.add_node("enrich", enrich)
    builder.add_node("appetite_and_eligibility", appetite_and_eligibility)
    builder.add_node("review_gate", review_gate)
    builder.add_node("route", route)
    builder.add_node("handoff", handoff)

    builder.add_edge(START, "ingest_and_classify")
    builder.add_edge("ingest_and_classify", "extract_and_structure")
    builder.add_conditional_edges("extract_and_structure", after_extract, {"continue": "enrich", "stop": END})
    builder.add_edge("enrich", "appetite_and_eligibility")
    builder.add_edge("appetite_and_eligibility", "review_gate")
    builder.add_edge("review_gate", "route")
    builder.add_edge("route", "handoff")
    builder.add_edge("handoff", END)
    return builder.compile(checkpointer=InMemorySaver())
