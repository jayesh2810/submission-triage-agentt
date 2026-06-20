from __future__ import annotations

import logging

from triage.models import EnrichmentResult, SubmissionRecord

logger = logging.getLogger("triage.enrichment")


def enrich_submission(submission: SubmissionRecord) -> EnrichmentResult:
    name = submission.named_insured or "Unknown insured"
    class_code = submission.class_code or "unknown"
    source_flags = set(submission.source_evidence_flags)
    logger.info("ENRICH start name=%s class_code=%s source_flags=%s", name, class_code, sorted(source_flags))

    claims_signal = {
        "summary": "No severe losses found in mock lookup.",
        "severity": "low",
        "loss_count_3yr": 0,
    }
    if "roofing" in source_flags:
        claims_signal = {
            "summary": "Mock lookup finds contractor-related slip/fall and water intrusion keywords.",
            "severity": "medium",
            "loss_count_3yr": 2,
        }

    result = EnrichmentResult(
        business_profile={
            "matched_name": name,
            "confidence": 0.86,
            "entity_type": "LLC" if "llc" in name.lower() else "unknown",
            "normalized_class_code": class_code,
        },
        claims_signal=claims_signal,
        geo_cat_context={
            "note": "Informational only. Geography is intentionally excluded from Step 4.",
            "cat_context": "standard",
        },
        notes=[
            "Mock enrichment only. No third-party APIs were called.",
            "Geo/cat context is displayed for handoff context, not guideline routing.",
        ],
    )
    logger.info(
        "ENRICH complete matched_name=%s normalized_class=%s claims_severity=%s",
        result.business_profile["matched_name"],
        result.business_profile["normalized_class_code"],
        result.claims_signal["severity"],
    )
    return result
