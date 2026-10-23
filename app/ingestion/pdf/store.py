"""Atomic, idempotent bank corpus publication using existing DTO/pgvector storage."""

import hashlib
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert

from app.core import tables as t
from app.ingestion.pdf.pipeline import (
    DEFAULT_CORPUS_CONFIG,
    PARSER_VERSION,
    config_digest,
    document_id,
    extract_pdf_documents_and_evidence,
    load_corpus_config,
)
from contracts.v1 import CompanyProfile, SourceRef, SourceVersion

IMPORT_VERSION = "pdf-corpus-2"
PUBLIC_SCOPE = "__public__"


def corpus_document_ids(config=None):
    config = config or load_corpus_config()
    return [document_id(e, config) for e in config["documents"].values()]


async def is_bank_document(c, document_id_value):
    return (
        await c.execute(
            select(t.documents.c.id)
            .join(t.source_versions, t.documents.c.source_id == t.source_versions.c.id)
            .where(
                t.documents.c.id == document_id_value,
                t.documents.c.id.in_(corpus_document_ids()),
                t.documents.c.company_id == PUBLIC_SCOPE,
                t.source_versions.c.import_version == IMPORT_VERSION,
            )
        )
    ).first() is not None


def prepare_corpus(pdf_dir: Path, *, config_path=DEFAULT_CORPUS_CONFIG, cache_dir=None, dpi=300):
    config = load_corpus_config(config_path)
    missing = sorted(set(config["documents"]) - {p.name for p in pdf_dir.glob("*.pdf")})
    if missing:
        raise ValueError(f"Missing approved bank PDFs: {missing}")
    prepared = []
    # Preflight ALL hashes before OCR; the task/key PDF is never passed to the parser.
    for name, entry in config["documents"].items():
        if hashlib.sha256((pdf_dir / name).read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError(f"Corpus hash mismatch: {name}")
    for name, entry in config["documents"].items():
        sid = "pdf_" + entry["sha256"][:24] + "_" + config_digest(config)[:16]
        source = SourceVersion(
            id=sid,
            domain="case",
            import_version=IMPORT_VERSION,
            available_at=None,
            ref=SourceRef(
                source_version_id=sid,
                original_filename=name,
                sha256=entry["sha256"],
                parser_version=PARSER_VERSION,
            ),
        )
        doc, evidence, theses = extract_pdf_documents_and_evidence(
            pdf_dir / name,
            source,
            dpi=dpi,
            config_path=config_path,
            cache_dir=cache_dir,
            corpus_config=config,
        )
        prepared.append((source, doc, evidence, theses))
    return prepared


async def publish_corpus(engine, prepared, encoder, *, checkpoint=None):
    # Heavy OCR/encoding happens before the transaction; any failure publishes nothing.
    vectors = {}
    for _, _, evidence, _ in prepared:
        if encoder.is_available:
            embeddings = encoder.encode_passages([e.quote for e in evidence])
            if len(embeddings) != len(evidence):
                raise ValueError("Embedding count mismatch")
            vectors.update({e.id: v for e, v in zip(evidence, embeddings, strict=True)})
    imported = unchanged = 0
    async with engine.begin() as c:
        await c.execute(select(func.pg_advisory_xact_lock(70602003)))
        for source, doc, evidence, _ in prepared:
            existing = (
                await c.execute(select(t.documents.c.payload).where(t.documents.c.id == doc.id))
            ).scalar_one_or_none()
            if existing is not None:
                if existing != doc.model_dump(mode="json"):
                    raise ValueError("Immutable document version collision")
                for ev in evidence:
                    old = (
                        await c.execute(
                            select(t.evidence.c.payload).where(t.evidence.c.id == ev.id)
                        )
                    ).scalar_one_or_none()
                    if old != ev.model_dump(mode="json"):
                        raise ValueError("Immutable evidence version collision")
                unchanged += 1
            else:
                await c.execute(
                    insert(t.source_versions)
                    .values(
                        id=source.id,
                        sha256=source.ref.sha256,
                        import_version=IMPORT_VERSION,
                        payload=source.model_dump(mode="json"),
                    )
                    .on_conflict_do_nothing()
                )
                # A typed non-client storage sentinel satisfies the legacy FK. It grants
                # no rights, has no financial attributes and never enters visible_profiles.
                sentinel = CompanyProfile(
                    company_id=PUBLIC_SCOPE,
                    snapshot_id="snap_public_bank_corpus",
                    source_version_id=source.id,
                    name="System bank corpus scope",
                    industry=None,
                    client_type="unknown",
                    status="stub",
                    features=[],
                    warnings=["storage_scope_not_a_company"],
                )
                await c.execute(
                    insert(t.companies)
                    .values(
                        id=sentinel.snapshot_id,
                        company_id=PUBLIC_SCOPE,
                        source_id=source.id,
                        payload=sentinel.model_dump(mode="json"),
                    )
                    .on_conflict_do_nothing()
                )
                await c.execute(
                    insert(t.documents).values(
                        id=doc.id,
                        source_id=source.id,
                        company_id=PUBLIC_SCOPE,
                        payload=doc.model_dump(mode="json"),
                    )
                )
                for ev in evidence:
                    await c.execute(
                        insert(t.evidence).values(
                            id=ev.id, document_id=doc.id, payload=ev.model_dump(mode="json")
                        )
                    )
                imported += 1
            for ev in evidence:
                if ev.id in vectors:
                    vector = vectors[ev.id]
                    if len(vector) != 384:
                        raise ValueError("Expected 384-dimensional embedding")
                    import math

                    if not all(math.isfinite(v) for v in vector):
                        raise ValueError("Non-finite embedding")
                    await c.execute(
                        text("UPDATE evidence SET embedding=CAST(:vector AS vector) WHERE id=:id"),
                        {"id": ev.id, "vector": str(vector)},
                    )
            if checkpoint:
                await checkpoint(c, imported)
    return {
        "imported": imported,
        "unchanged": unchanged,
        "evidence": sum(len(p[2]) for p in prepared),
        "embedded": len(vectors),
    }
