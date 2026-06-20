from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from triage.models import FailureAnalysis, GuidelineResult, RoutingResult, SubmissionRecord

ROOT = Path(__file__).parent.parent
GUIDELINE_PATH = ROOT / "config" / "guidelines.yaml"

logger = logging.getLogger("triage.guidelines")


def load_guidelines() -> dict[str, Any]:
    logger.info("GUIDELINES load path=%s", GUIDELINE_PATH)
    with open(GUIDELINE_PATH) as f:
        cfg = yaml.safe_load(f)
    logger.info(
        "GUIDELINES load complete class_codes=%s required_fields=%s",
        len(cfg.get("class_codes", {})),
        cfg.get("required_fields", []),
    )
    return cfg


def evaluate_guidelines(submission: SubmissionRecord) -> GuidelineResult:
    logger.info(
        "GUIDELINES evaluate start named_insured=%s class_code=%s line=%s limits=%s",
        submission.named_insured or "-",
        submission.class_code or "-",
        submission.line_of_business,
        submission.requested_limits,
    )
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

    result = GuidelineResult(
        class_code_status=class_status,
        limits_status=limits_status,
        prohibited_class=class_status == "prohibited",
        missing_fields=missing,
        reasons=reasons,
    )
    logger.info(
        "GUIDELINES evaluate complete class_status=%s limits_status=%s prohibited=%s missing=%s reasons=%s",
        result.class_code_status,
        result.limits_status,
        result.prohibited_class,
        result.missing_fields,
        result.reasons,
    )
    return result


def route_submission(guidelines: GuidelineResult) -> RoutingResult:
    logger.info(
        "ROUTE evaluate start class_status=%s limits_status=%s prohibited=%s missing=%s",
        guidelines.class_code_status,
        guidelines.limits_status,
        guidelines.prohibited_class,
        guidelines.missing_fields,
    )
    if guidelines.missing_fields:
        result = RoutingResult(
            queue="missing_info",
            priority="high",
            rationale="Required submission fields are missing.",
            reviewer_notes=[f"Missing: {', '.join(guidelines.missing_fields)}"],
        )
        logger.info("ROUTE selected queue=%s priority=%s rationale=%s", result.queue, result.priority, result.rationale)
        return result
    if guidelines.prohibited_class:
        result = RoutingResult(
            queue="prohibited_class_review",
            priority="high",
            rationale="Normalized class code is prohibited by local guidelines.",
            reviewer_notes=guidelines.reasons,
        )
        logger.info("ROUTE selected queue=%s priority=%s rationale=%s", result.queue, result.priority, result.rationale)
        return result
    if guidelines.class_code_status in {"senior_review", "unknown"} or guidelines.limits_status in {"senior_review", "unknown"}:
        result = RoutingResult(
            queue="senior_uw_review",
            priority="normal",
            rationale="Guidelines require senior underwriter triage.",
            reviewer_notes=guidelines.reasons,
        )
        logger.info("ROUTE selected queue=%s priority=%s rationale=%s", result.queue, result.priority, result.rationale)
        return result
    result = RoutingResult(
        queue="ready_for_uw",
        priority="normal",
        rationale="Submission appears complete and within local triage guidelines.",
        reviewer_notes=guidelines.reasons,
    )
    logger.info("ROUTE selected queue=%s priority=%s rationale=%s", result.queue, result.priority, result.rationale)
    return result


def analyze_failure(submission: SubmissionRecord, routing: RoutingResult) -> FailureAnalysis:
    logger.info(
        "FAILURE_ANALYSIS start class_code=%s queue=%s source_flags=%s",
        submission.class_code or "-",
        routing.queue,
        submission.source_evidence_flags,
    )
    cfg = load_guidelines()
    red_flags = cfg.get("source_evidence_red_flags", {})
    for flag in submission.source_evidence_flags:
        if flag in red_flags and routing.queue == "ready_for_uw":
            expected = red_flags[flag]["expected_route"]
            result = FailureAnalysis(
                has_control_gap=True,
                agent_believed=(
                    f"Class code {submission.class_code} "
                    f"({submission.business_description or 'no description'}) was acceptable."
                ),
                source_evidence_suggested=flag,
                control_gap=red_flags[flag]["reason"],
                expected_route=expected,
            )
            logger.info(
                "FAILURE_ANALYSIS control_gap flag=%s expected_route=%s",
                flag,
                result.expected_route,
            )
            return result
    logger.info("FAILURE_ANALYSIS complete no_control_gap")
    return FailureAnalysis(has_control_gap=False)
