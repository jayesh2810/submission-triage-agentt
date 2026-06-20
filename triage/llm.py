from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx
from pydantic import ValidationError

from triage.models import BrokerMetadata, SubmissionRecord, UploadedDocument

logger = logging.getLogger("triage.llm")


DEFAULT_MODEL = "gemini-3.5-flash"


class GeminiExtractionError(RuntimeError):
    """Raised when Gemini extraction cannot produce a valid submission record."""


def gemini_status() -> dict[str, Any]:
    return {
        "provider": "gemini",
        "model": os.getenv("GEMINI_MODEL", DEFAULT_MODEL),
        "api_key_set": bool(os.getenv("GEMINI_API_KEY")),
    }


def _combined_text(docs: list[UploadedDocument], metadata: BrokerMetadata) -> str:
    chunks = []
    if metadata.subject:
        chunks.append(f"Subject: {metadata.subject}")
    if metadata.notes:
        chunks.append(f"Broker notes:\n{metadata.notes}")
    for doc in docs:
        if doc.text:
            chunks.append(f"Document: {doc.filename}\n{doc.text}")
    return "\n\n".join(chunks)


def extract_submission(docs: list[UploadedDocument], metadata: BrokerMetadata) -> SubmissionRecord:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        logger.info("LLM extract blocked missing_api_key docs=%s broker=%s", len(docs), metadata.broker_name or "-")
        raise GeminiExtractionError("GEMINI_API_KEY is not set. Add it to .env and restart the server.")
    model = os.getenv("GEMINI_MODEL", DEFAULT_MODEL)
    combined_text = _combined_text(docs, metadata)
    logger.info(
        "LLM extract start provider=gemini model=%s docs=%s text_chars=%s broker=%s",
        model,
        len(docs),
        len(combined_text),
        metadata.broker_name or "-",
    )

    prompt = f"""
You are extracting a small commercial insurance submission for a triage workflow.
Return only JSON matching the provided schema.

Important:
- Do not make underwriting decisions.
- Do not apply geography rules.
- Return a single JSON object only, without Markdown fences or commentary.
- Use the exact field names from the schema.
- Omit unknown nullable scalar fields instead of returning null.
- Capture source_evidence_flags if source text mentions roofing, demolition,
  asbestos, nightclub, security, or other hazardous/prohibited-looking work.
- If the source is ambiguous, preserve ambiguity in extraction_notes and lower
  the relevant field confidence.
- Extract class_code/SIC/NAICS only when present or strongly implied.

Broker metadata:
{metadata.model_dump_json()}

Text from documents:
{combined_text}

JSON shape:
{{
  "named_insured": "string",
  "broker_name": "string",
  "submission_type": "new_business | renewal | endorsement | unknown",
  "line_of_business": "general_liability | property | bop | workers_comp | unknown",
  "business_description": "string",
  "class_code": "string",
  "sic_code": "string",
  "naics_code": "string",
  "requested_limits": {{"per_occurrence": 0, "aggregate": 0, "property_limit": 0, "tiv": 0}},
  "locations": [{{"address": "string", "city": "string", "state": "string", "zip_code": "string"}}],
  "payroll": 0,
  "revenue": 0,
  "tiv": 0,
  "prior_losses": [{{"date": "string", "description": "string", "amount": 0}}],
  "missing_required_fields": ["string"],
  "field_confidence": [{{"field": "string", "confidence": 0.0, "evidence": "string"}}],
  "source_evidence_flags": ["string"],
  "extraction_notes": "string"
}}
"""

    parts: list[dict[str, Any]] = [{"text": prompt}]
    for doc in docs:
        if doc.mime_type == "application/pdf" and doc.raw_bytes_b64:
            logger.info("LLM attach pdf filename=%s bytes=%s", doc.filename, doc.size)
            parts.insert(
                0,
                {
                    "inline_data": {
                        "mime_type": "application/pdf",
                        "data": doc.raw_bytes_b64,
                    }
                },
            )

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "responseMimeType": "application/json",
        },
    }

    try:
        logger.info("LLM request sending model=%s parts=%s timeout_seconds=45", model, len(parts))
        response = httpx.post(
            url,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json=payload,
            timeout=45,
        )
        logger.info("LLM response received status=%s bytes=%s", response.status_code, len(response.content))
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        detail = exc.response.text[:500] if exc.response is not None else str(exc)
        logger.info("LLM response http_error status=%s detail=%s", status, detail)
        raise GeminiExtractionError(f"Gemini API request failed with HTTP {status}: {detail}") from exc
    except httpx.HTTPError as exc:
        logger.info("LLM request transport_error error=%s", exc)
        raise GeminiExtractionError(f"Gemini API request failed: {exc}") from exc

    try:
        data = response.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        logger.info("LLM response text extracted chars=%s", len(text))
        submission = SubmissionRecord.model_validate_json(text)
        logger.info(
            "LLM validation complete named_insured=%s class_code=%s line=%s missing=%s",
            submission.named_insured or "-",
            submission.class_code or "-",
            submission.line_of_business,
            submission.missing_required_fields,
        )
        return submission
    except (KeyError, IndexError, TypeError, json.JSONDecodeError, ValidationError) as exc:
        logger.info("LLM validation failed error=%s", exc)
        raise GeminiExtractionError(f"Gemini response could not be parsed into a submission record: {exc}") from exc
