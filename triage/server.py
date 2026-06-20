from __future__ import annotations

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


@app.get("/", response_class=HTMLResponse)
async def queue_page(request: Request):
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
    metadata = BrokerMetadata(
        broker_name=broker_name or None,
        broker_email=broker_email or None,
        subject=subject or None,
        notes=notes or None,
    )
    docs = await read_uploads(files)
    if notes and not docs:
        from triage.models import UploadedDocument

        docs = [
            UploadedDocument(
                filename="broker-notes.txt",
                mime_type="text/plain",
                size=len(notes.encode()),
                text=notes,
            )
        ]
    result = store.start(metadata, docs)
    return RedirectResponse(url=f"/handoff/{result['submission_id']}", status_code=303)


@app.post("/demo/failure")
async def create_failure_demo():
    from triage.models import UploadedDocument

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
    return RedirectResponse(url=f"/handoff/{result['submission_id']}", status_code=303)


@app.get("/submissions/{submission_id}")
async def get_submission(submission_id: str):
    state = store.get(submission_id)
    if not state:
        raise HTTPException(status_code=404, detail="Submission not found")
    return JSONResponse(_display_state(state))


@app.get("/review/{submission_id}", response_class=HTMLResponse)
async def review_page(request: Request, submission_id: str):
    state = store.get(submission_id)
    if not state:
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
    state = store.get(submission_id)
    if not state:
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
    result = store.resume(submission_id, payload)
    return RedirectResponse(url=f"/handoff/{result['submission_id']}", status_code=303)


@app.get("/handoff/{submission_id}", response_class=HTMLResponse)
async def handoff_page(request: Request, submission_id: str):
    state = store.get(submission_id)
    if not state:
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
    state = store.get(submission_id)
    if not state:
        raise HTTPException(status_code=404, detail="Submission not found")
    if state.get("handoff"):
        return JSONResponse(state["handoff"])
    return JSONResponse(_display_state(state))


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "llm": gemini_status(),
        "in_memory_submissions": len(store.submissions),
    }
