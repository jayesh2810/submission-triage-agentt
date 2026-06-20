from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from triage.models import FailureAnalysis, GuidelineResult, RoutingResult, SubmissionRecord

ROOT = Path(__file__).parent.parent
GUIDELINE_PATH = ROOT / "config" / "guidelines.yaml"


def load_guidelines() -> dict[str, Any]:
    with open(GUIDELINE_PATH) as f:
        return yaml.safe_load(f)


def evaluate_guidelines(submission: SubmissionRecord) -> GuidelineResult:
    cfg = load_guidelines()
    reasons: list[str] = []
    missing = list(submission.missing_required_fields)
    for field in cfg.get("required_fields", []):
        if not getattr(submission, field, None) and field not in missing:
            missing.append(field)

    class_cfg = cfg.get("class_codes", {}).get(str(submission.class_code or ""))
    if class_cfg:
        class_status = class_cfg["status"]
        reasons.append(f"Class {submission.class_code}: {class_cfg['label']} - {class_cfg['notes']}")
    else:
        class_status = "unknown"
        reasons.append("Class code is missing or not found in local guidelines.")

    limits_status = "standard"
    lob = submission.line_of_business
    limit_cfg = cfg.get("limits", {}).get(lob, {})
    if lob == "general_liability":
        occ = submission.requested_limits.get("per_occurrence")
        threshold = limit_cfg.get("senior_review_above")
        if occ and threshold and occ > threshold:
            limits_status = "senior_review"
            reasons.append(f"Per-occurrence limit {occ:,.0f} exceeds standard authority.")
    elif lob in {"bop", "property"}:
        threshold = limit_cfg.get("senior_review_above")
        if submission.tiv and threshold and submission.tiv > threshold:
            limits_status = "senior_review"
            reasons.append(f"TIV {submission.tiv:,.0f} exceeds standard authority.")
    elif lob == "unknown":
        limits_status = "unknown"
        reasons.append("Line of business is unknown.")

    return GuidelineResult(
        class_code_status=class_status,
        limits_status=limits_status,
        prohibited_class=class_status == "prohibited",
        missing_fields=missing,
        reasons=reasons,
    )


def route_submission(guidelines: GuidelineResult) -> RoutingResult:
    if guidelines.missing_fields:
        return RoutingResult(
            queue="missing_info",
            priority="high",
            rationale="Required submission fields are missing.",
            reviewer_notes=[f"Missing: {', '.join(guidelines.missing_fields)}"],
        )
    if guidelines.prohibited_class:
        return RoutingResult(
            queue="prohibited_class_review",
            priority="high",
            rationale="Normalized class code is prohibited by local guidelines.",
            reviewer_notes=guidelines.reasons,
        )
    if guidelines.class_code_status in {"senior_review", "unknown"} or guidelines.limits_status in {"senior_review", "unknown"}:
        return RoutingResult(
            queue="senior_uw_review",
            priority="normal",
            rationale="Guidelines require senior underwriter triage.",
            reviewer_notes=guidelines.reasons,
        )
    return RoutingResult(
        queue="ready_for_uw",
        priority="normal",
        rationale="Submission appears complete and within local triage guidelines.",
        reviewer_notes=guidelines.reasons,
    )


def analyze_failure(submission: SubmissionRecord, routing: RoutingResult) -> FailureAnalysis:
    cfg = load_guidelines()
    red_flags = cfg.get("source_evidence_red_flags", {})
    for flag in submission.source_evidence_flags:
        if flag in red_flags and routing.queue == "ready_for_uw":
            expected = red_flags[flag]["expected_route"]
            return FailureAnalysis(
                has_control_gap=True,
                agent_believed=(
                    f"Class code {submission.class_code} "
                    f"({submission.business_description or 'no description'}) was acceptable."
                ),
                source_evidence_suggested=flag,
                control_gap=red_flags[flag]["reason"],
                expected_route=expected,
            )
    return FailureAnalysis(has_control_gap=False)

