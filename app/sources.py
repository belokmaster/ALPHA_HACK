"""One authorization boundary for stored fragments, documents and source assets."""

from sqlalchemy import select

from app.auth.service import authorize
from app.core import tables as t
from app.core.errors import AppError
from app.ingestion.news.service import is_public
from app.ingestion.pdf.store import is_bank_document
from contracts.v1 import Document, ErrorCode, Evidence


async def document(c, context, document_id):
    row = (
        (await c.execute(select(t.documents).where(t.documents.c.id == document_id)))
        .mappings()
        .first()
    )
    if row is None:
        raise AppError(ErrorCode.not_found, "Resource not found", 404)
    public = await is_public(c, document_id) or await is_bank_document(c, document_id)
    company = context.company_id if public else row["company_id"]
    if company is None:
        raise AppError(ErrorCode.not_found, "Resource not found", 404)
    await authorize(c, context, company, "sources.read")
    return Document.model_validate(row["payload"])


async def evidence(c, context, evidence_id):
    row = (
        (await c.execute(select(t.evidence).where(t.evidence.c.id == evidence_id)))
        .mappings()
        .first()
    )
    if row is None:
        raise AppError(ErrorCode.not_found, "Resource not found", 404)
    doc = await document(c, context, row["document_id"])
    ev = Evidence.model_validate(row["payload"])
    if ev.source_version_id != doc.source_version_id:
        raise AppError(ErrorCode.data_conflict, "Fragment/source version differs", 409)
    # Check addressability, not just presence of a foreign key or quote somewhere.
    try:
        if ev.span.startswith("page_chars:"):
            marker = f"[Стр. {ev.page}]\n"
            page = doc.text.split(marker, 1)[1].split("\n\n[Стр.", 1)[0]
            _, start, end = ev.span.split(":")
            match = page[int(start) : int(end)] if 0 <= int(start) < int(end) <= len(page) else None
        elif ev.span.startswith("chars:"):
            _, start, end = ev.span.split(":")
            match = (
                doc.text[int(start) : int(end)]
                if 0 <= int(start) < int(end) <= len(doc.text)
                else None
            )
        else:
            match = ev.quote if ev.quote in doc.text else None
    except (IndexError, ValueError):
        match = None
    if match != ev.quote:
        raise AppError(ErrorCode.data_conflict, "Fragment does not match stored text", 409)
    return ev
