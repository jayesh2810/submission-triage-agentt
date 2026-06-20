from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv

from triage.documents import read_uploads, safe_document_dump
from triage.llm import gemini_status
from triage.models import BrokerMetadata, SubmissionRecord
from triage.store import AppStore

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")


def _configure_logging() -> logging.Logger:
    logger = logging.getLogger("triage")
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


logger = _configure_logging()

app = FastAPI(title="Submission Triage Agent v2", version="0.1.0")
app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")
templates = Jinja2Templates(directory=str(ROOT / "templates"))
store = AppStore()


def _display_state(state: dict[str, Any]) -> dict[str, Any]:
    item = dict(state)
    if "documents" in item:
        item["documents"] = safe_document_dump(item["documents"])
    return item


def _queue_counts() -> dict[str, int]:
    counts = {
        "ready_for_uw": 0,
        "missing_info": 0,
        "senior_uw_review": 0,
        "prohibited_class_review": 0,
        "needs_review": 0,
    }
    for sub in store.list():
        if sub.get("status") == "needs_review":
            counts["needs_review"] += 1
        queue = sub.get("queue")
        if queue in counts:
            counts[queue] += 1
    return counts


def _is_pdf_upload(file: UploadFile) -> bool:
    filename = (file.filename or "").lower()
    return filename.endswith(".pdf") or file.content_type == "application/pdf"


@app.get("/", response_class=HTMLResponse)
async def queue_page(request: Request):
    logger.info("QUEUE_PAGE requested submissions=%s", len(store.submissions))
    return templates.TemplateResponse(
        request,
        "queue.html",
        {
            "request": request,
            "submissions": [_display_state(s) for s in store.list()],
            "counts": _queue_counts(),
            "llm": gemini_status(),
        },
    )


@app.post("/submissions")
async def create_submission(
    broker_name: str = Form(default=""),
    broker_email: str = Form(default=""),
    subject: str = Form(default=""),
    notes: str = Form(default=""),
    files: list[UploadFile] = File(default=[]),
):
    upload_names = [f.filename or "document" for f in files]
    logger.info(
        "SUBMIT received broker=%s subject=%s notes_chars=%s files=%s",
        broker_name or "-",
        subject or "-",
        len(notes),
        upload_names,
    )
    metadata = BrokerMetadata(
        broker_name=broker_name or None,
        broker_email=broker_email or None,
        subject=subject or None,
        notes=notes or None,
    )
    if not files:
        logger.info("SUBMIT rejected no_files")
        raise HTTPException(status_code=400, detail="Upload at least one PDF file.")
    invalid_files = [f.filename or "document" for f in files if not _is_pdf_upload(f)]
    if invalid_files:
        logger.info("SUBMIT rejected non_pdf_files=%s", invalid_files)
        raise HTTPException(status_code=400, detail=f"Only PDF files are supported: {', '.join(invalid_files)}")
    logger.info("SUBMIT reading uploaded documents")
    docs = await read_uploads(files)
    logger.info("SUBMIT parsed documents count=%s", len(docs))
    if notes and not docs:
        from triage.models import UploadedDocument

        logger.info("SUBMIT no files found; creating broker-notes.txt from notes")
        docs = [
            UploadedDocument(
                filename="broker-notes.txt",
                mime_type="text/plain",
                size=len(notes.encode()),
                text=notes,
            )
        ]
    logger.info("SUBMIT starting graph broker=%s docs=%s", metadata.broker_name or "-", len(docs))
    result = store.start(metadata, docs)
    logger.info(
        "SUBMIT finished submission_id=%s status=%s queue=%s redirect=/handoff/%s",
        result.get("submission_id"),
        result.get("status"),
        result.get("queue"),
        result.get("submission_id"),
    )
    return RedirectResponse(url=f"/handoff/{result['submission_id']}", status_code=303)


@app.post("/demo/failure")
async def create_failure_demo():
    from triage.models import UploadedDocument

    logger.info("DEMO_FAILURE submit requested")
    notes = """DEMO_FAILURE_TRIGGER
Named Insured: Apex Facilities Consulting LLC
Broker: Northstar Retail Brokers
Line: General Liability
Requested limit: $1,000,000 per occurrence
Description: The insured provides facilities consulting, roof inspection,
roof repair coordination, and occasional roofing installation supervision.
The submission includes payroll for crews working at height.
"""
    metadata = BrokerMetadata(
        broker_name="Northstar Retail Brokers",
        broker_email="broker@example.com",
        subject="New business submission - Apex Facilities Consulting",
        notes=notes,
    )
    docs = [UploadedDocument(filename="apex-demo.txt", mime_type="text/plain", size=len(notes), text=notes)]
    result = store.start(metadata, docs)
    logger.info(
        "DEMO_FAILURE finished submission_id=%s status=%s queue=%s",
        result.get("submission_id"),
        result.get("status"),
        result.get("queue"),
    )
    return RedirectResponse(url=f"/handoff/{result['submission_id']}", status_code=303)


@app.get("/submissions/{submission_id}")
async def get_submission(submission_id: str):
    logger.info("STATE_JSON requested submission_id=%s", submission_id)
    state = store.get(submission_id)
    if not state:
        logger.info("STATE_JSON not_found submission_id=%s", submission_id)
        raise HTTPException(status_code=404, detail="Submission not found")
    return JSONResponse(_display_state(state))


@app.get("/review/{submission_id}", response_class=HTMLResponse)
async def review_page(request: Request, submission_id: str):
    logger.info("REVIEW_PAGE requested submission_id=%s", submission_id)
    state = store.get(submission_id)
    if not state:
        logger.info("REVIEW_PAGE not_found submission_id=%s", submission_id)
        raise HTTPException(status_code=404, detail="Submission not found")
    return templates.TemplateResponse(
        request,
        "review.html",
        {
            "request": request,
            "state": _display_state(state),
            "counts": _queue_counts(),
            "llm": gemini_status(),
        },
    )


@app.post("/review/{submission_id}/resume")
async def resume_review(
    submission_id: str,
    named_insured: str = Form(default=""),
    class_code: str = Form(default=""),
    line_of_business: str = Form(default="general_liability"),
    business_description: str = Form(default=""),
    action: str = Form(default="resume"),
):
    logger.info("REVIEW_RESUME received submission_id=%s action=%s", submission_id, action)
    state = store.get(submission_id)
    if not state:
        logger.info("REVIEW_RESUME not_found submission_id=%s", submission_id)
        raise HTTPException(status_code=404, detail="Submission not found")

    interrupt_payload = state.get("interrupt", {})
    submission_data = interrupt_payload.get("submission") or state.get("submission") or {}
    submission = SubmissionRecord.model_validate(submission_data)
    submission.named_insured = named_insured or submission.named_insured
    submission.class_code = class_code or submission.class_code
    submission.line_of_business = line_of_business  # type: ignore[assignment]
    submission.business_description = business_description or submission.business_description
    submission.missing_required_fields = []

    payload = {
        "action": action,
        "submission": submission.model_dump(),
        "reviewer_note": "Manual browser review completed.",
    }
    logger.info(
        "REVIEW_RESUME applying edits submission_id=%s named_insured=%s class_code=%s line=%s",
        submission_id,
        submission.named_insured or "-",
        submission.class_code or "-",
        submission.line_of_business,
    )
    result = store.resume(submission_id, payload)
    logger.info(
        "REVIEW_RESUME finished submission_id=%s status=%s queue=%s",
        result.get("submission_id"),
        result.get("status"),
        result.get("queue"),
    )
    return RedirectResponse(url=f"/handoff/{result['submission_id']}", status_code=303)


@app.get("/handoff/{submission_id}", response_class=HTMLResponse)
async def handoff_page(request: Request, submission_id: str):
    logger.info("HANDOFF_PAGE requested submission_id=%s", submission_id)
    state = store.get(submission_id)
    if not state:
        logger.info("HANDOFF_PAGE not_found submission_id=%s", submission_id)
        raise HTTPException(status_code=404, detail="Submission not found")
    return templates.TemplateResponse(
        request,
        "handoff.html",
        {
            "request": request,
            "state": _display_state(state),
            "counts": _queue_counts(),
            "llm": gemini_status(),
        },
    )


@app.get("/handoff/{submission_id}.json")
async def handoff_json(submission_id: str):
    logger.info("HANDOFF_JSON requested submission_id=%s", submission_id)
    state = store.get(submission_id)
    if not state:
        logger.info("HANDOFF_JSON not_found submission_id=%s", submission_id)
        raise HTTPException(status_code=404, detail="Submission not found")
    if state.get("handoff"):
        return JSONResponse(state["handoff"])
    return JSONResponse(_display_state(state))


@app.get("/health")
async def health():
    logger.info("HEALTH requested submissions=%s", len(store.submissions))
    return {
        "status": "ok",
        "llm": gemini_status(),
        "in_memory_submissions": len(store.submissions),
    }
