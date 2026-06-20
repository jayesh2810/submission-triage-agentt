from __future__ import annotations

import base64
from pathlib import Path

from docx import Document
from fastapi import UploadFile

from triage.models import UploadedDocument


PDF_TYPES = {"application/pdf"}
DOCX_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/octet-stream",
}
TXT_TYPES = {"text/plain", "text/markdown"}


def _guess_mime(filename: str, declared: str | None) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        return "application/pdf"
    if suffix == ".docx":
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if suffix in {".txt", ".md"}:
        return "text/plain"
    return declared or "application/octet-stream"


def _docx_text(raw: bytes) -> str:
    import io

    doc = Document(io.BytesIO(raw))
    paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    return "\n".join(paragraphs)


async def read_uploads(files: list[UploadFile]) -> list[UploadedDocument]:
    docs: list[UploadedDocument] = []
    for f in files:
        raw = await f.read()
        mime = _guess_mime(f.filename or "document", f.content_type)
        text: str | None = None
        if mime in TXT_TYPES:
            text = raw.decode("utf-8", errors="replace")
        elif mime in DOCX_TYPES and (f.filename or "").lower().endswith(".docx"):
            text = _docx_text(raw)
        elif mime not in PDF_TYPES:
            text = raw.decode("utf-8", errors="replace")
        docs.append(
            UploadedDocument(
                filename=f.filename or "document",
                mime_type=mime,
                size=len(raw),
                text=text,
                raw_bytes_b64=base64.b64encode(raw).decode("ascii"),
            )
        )
    return docs


def safe_document_dump(docs: list[dict]) -> list[dict]:
    safe = []
    for d in docs:
        item = dict(d)
        item.pop("raw_bytes_b64", None)
        safe.append(item)
    return safe

