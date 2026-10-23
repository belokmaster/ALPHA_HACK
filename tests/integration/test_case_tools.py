from contextlib import asynccontextmanager

import pytest
from sqlalchemy import delete, func, insert, select

from app.auth.service import current_context
from app.core import tables as t
from app.core.errors import AppError
from app.graph.service import CompanyTools
from app.ingestion.csv.audit import audited_inputs
from app.ingestion.csv.importer import import_snapshot
from contracts.v1 import CompanyProfile, Grant, RelationPath

pytestmark = pytest.mark.integration


class ConnectionEngine:
    def __init__(self, connection):
        self.connection = connection

    @asynccontextmanager
    async def connect(self):
        yield self.connection

    begin = connect


@pytest.fixture
async def connection(db):
    async with db.connect() as conn:
        transaction = await conn.begin()
        try:
            yield conn
        finally:
            await transaction.rollback()


async def test_case_import_idempotence_quarantine_and_tools(connection, case_csv):
    conn = connection
    with audited_inputs(case_csv) as (index, report):
        result = await import_snapshot(conn, index, report)
        again = await import_snapshot(conn, index, report)
    assert result["status"] == "imported" and again["status"] == "already_imported"
    assert result["grants_created"] == 0
    assert (await conn.execute(select(func.count()).select_from(t.grants))).scalar_one() == 4
    rows = (await conn.execute(select(t.companies.c.payload))).scalars()
    profiles = {p.company_id: p for p in map(CompanyProfile.model_validate, rows)}
    assert profiles["Uq"].status == "quarantined" and not profiles["Uq"].features
    assert profiles["Ustub"].status == "stub"
    revenue = next(f for f in profiles["U1"].features if f.name == "REVENUE_COMPANY")
    assert revenue.raw_value == "1000" and revenue.value is None
    assert revenue.provenance[0].row == 2 and revenue.available_at is None
    rels = [
        RelationPath.model_validate(p)
        for p in (
            await conn.execute(
                select(t.relations.c.payload).where(
                    t.relations.c.source_id.in_(result["source_version_ids"])
                )
            )
        ).scalars()
    ]
    payments = [r for r in rels if r.kind == "observed_payment"]
    assert sorted(r.nodes for r in payments) == [["U1", "U2"], ["U1", "Ustub"]]
    assert any(r.kind == "self_transfer" for r in rels)
    assert all(r.economic_role == "unknown" for r in rels)
    assert (
        await conn.execute(select(func.count()).select_from(t.quality_issues))
    ).scalar_one() == 3
    for cid in ("U1", "U2"):
        grant = Grant(company_id=cid, scopes=["profile.read", "relations.read"])
        await conn.execute(
            insert(t.grants).values(
                principal_id="alice", company_id=cid, payload=grant.model_dump(mode="json")
            )
        )
    ctx = await current_context(conn, "alice")
    tools = CompanyTools(ConnectionEngine(conn))
    profile = await tools.get_profile(ctx, "U1")
    assert profile.group.visible_company_ids == ["U1", "U2"]
    assert profile.group.observed_group_revenue_sum is None
    paths = await tools.get_relations(ctx, "U1")
    assert {r.kind for r in paths} == {"shared_person", "group", "observed_payment"}
    assert any(r.nodes == ["U1", "F1", "U2"] for r in paths)
    assert "Uhidden" not in str(paths) and "Ustub" not in str(paths)
    # Context still contains the old grant. Server revocation must take immediate effect.
    await conn.execute(
        delete(t.grants).where(t.grants.c.principal_id == "alice", t.grants.c.company_id == "U2")
    )
    assert (await tools.get_profile(ctx, "U1")).group.visible_company_ids == ["U1"]
    assert await tools.get_relations(ctx, "U1") == []
    with pytest.raises(AppError) as denied:
        await tools.get_relations(ctx, "U2")
    assert denied.value.status == 404


async def test_changed_dataset_is_rejected_without_replacing_snapshots(connection, case_csv):
    from app.ingestion.csv.audit import FILES

    with audited_inputs(case_csv) as (index, report):
        await import_snapshot(connection, index, report)
    path = case_csv / FILES["companies"]
    path.write_text(path.read_text().replace("1000", "1001"))
    with audited_inputs(case_csv) as (index, report):
        with pytest.raises(ValueError, match="versioned company migration"):
            await import_snapshot(connection, index, report)
    profile = CompanyProfile.model_validate(
        (
            await connection.execute(
                select(t.companies.c.payload).where(t.companies.c.company_id == "U1")
            )
        ).scalar_one()
    )
    assert next(f.raw_value for f in profile.features if f.name == "REVENUE_COMPANY") == "1000"


async def test_legacy_case_import_requires_explicit_semantic_migration(connection, case_csv):
    from app.ingestion.csv.importer import IMPORT_VERSION

    await connection.execute(
        insert(t.source_versions).values(
            id="legacy_case", sha256="9" * 64, import_version="case-csv-1", payload={}
        )
    )
    with audited_inputs(case_csv) as (index, report):
        assert report["files"]["payments"]["period"]["end"] is None
        assert IMPORT_VERSION == "case-csv-2"
        with pytest.raises(ValueError, match="versioned company migration"):
            await import_snapshot(connection, index, report)


async def test_retrieval_server_acl_time_and_vector_fallback(connection):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import text

    from app.retrieval.service import HybridRetriever, LocalE5Encoder
    from contracts.v1 import AnalysisRequest, Document, Evidence, TemporalMode

    conn = connection
    now = datetime(2026, 10, 21, tzinfo=UTC)
    from contracts.v1 import SourceRef, SourceVersion

    sv = SourceVersion(
        id="case_retrieval_sv",
        domain="case",
        available_at=now - timedelta(days=1),
        ref=SourceRef(
            source_version_id="case_retrieval_sv", original_filename="case-test", sha256="a" * 64
        ),
    )
    await conn.execute(
        insert(t.source_versions).values(
            id=sv.id, sha256="a" * 64, import_version="test", payload=sv.model_dump(mode="json")
        )
    )
    for key, company, available in (
        ("past", "demo_a", now - timedelta(days=1)),
        ("future", "demo_a", now + timedelta(days=1)),
        ("unknown", "demo_a", None),
        ("hidden", "demo_hidden", now - timedelta(days=1)),
    ):
        doc = Document(
            id="retrieval_" + key,
            source_version_id="case_retrieval_sv",
            domain="case",
            title=key,
            publisher="Test",
            published_at=available,
            available_at=available,
            text="ставка кредит",
        )
        ev = Evidence(
            id=doc.id,
            document_version_id=doc.id,
            source_version_id="case_retrieval_sv",
            quote="ставка кредит " + key,
            page=1,
            span="test",
            value_kind="fact",
            quality="unverified",
            domain="case",
            available_at=available,
        )
        await conn.execute(
            insert(t.documents).values(
                id=doc.id,
                source_id="case_retrieval_sv",
                company_id=company,
                payload=doc.model_dump(mode="json"),
            )
        )
        await conn.execute(
            insert(t.evidence).values(
                id=ev.id, document_id=doc.id, payload=ev.model_dump(mode="json")
            )
        )
        vector = "[1," + ",".join(["0"] * 383) + "]"
        await conn.execute(
            text("UPDATE evidence SET embedding=CAST(:v AS vector) WHERE id=:id"),
            {"v": vector, "id": ev.id},
        )
    ctx = await current_context(conn, "alice")
    # Forge an additional grant: it must not enlarge server-authorized candidates.
    ctx.grants.append(Grant(company_id="demo_hidden", scopes=["sources.read"]))
    req = AnalysisRequest(
        event_id="rate_hold", context_version=ctx.version, as_of=now, temporal_mode="strict_as_of"
    )
    engine = ConnectionEngine(conn)
    retriever = HybridRetriever(engine, LocalE5Encoder(force_disable=True))
    strict = await retriever.search_with_meta(ctx, req, "ставка")
    assert [e.id for e in strict.evidence] == ["retrieval_past"]
    assert strict.retrieval_mode == "lexical_fallback"
    snapshot = await retriever.search(
        ctx, req.model_copy(update={"temporal_mode": TemporalMode.snapshot_demo}), "ставка"
    )
    assert {e.id for e in snapshot} == {"retrieval_past", "retrieval_unknown"}

    class FakeLocalEncoder:
        is_available = True

        def encode_query(self, query):
            return [1.0] + [0.0] * 383

    retriever = HybridRetriever(engine, FakeLocalEncoder())
    for mode in ("exact", "hnsw"):
        result = await retriever.search_with_meta(ctx, req, "ставка", mode_preference=mode)
        assert result.retrieval_mode == "hybrid_" + mode
        assert [e.id for e in result.evidence] == ["retrieval_past"]

    class BadVector(FakeLocalEncoder):
        def encode_query(self, query):
            return [1.0, 0.0]

    failed = await HybridRetriever(engine, BadVector()).search_with_meta(ctx, req, "ставка")
    assert failed.retrieval_mode == "lexical_fallback"
    assert [e.id for e in failed.evidence] == ["retrieval_past"]
    assert (await conn.execute(text("SELECT 1"))).scalar_one() == 1
    with pytest.raises(AppError) as stale:
        await retriever.search(ctx, req.model_copy(update={"context_version": 999}), "ставка")
    assert stale.value.status == 409
    await conn.execute(
        delete(t.grants).where(
            t.grants.c.principal_id == "alice", t.grants.c.company_id == "demo_a"
        )
    )
    with pytest.raises(AppError) as revoked:
        await retriever.search(ctx, req, "ставка")
    assert revoked.value.status == 404


async def test_missing_group_does_not_merge_unrelated_companies(connection):
    from sqlalchemy import update

    conn = connection
    for cid in ("demo_a", "demo_b"):
        raw = (
            await conn.execute(select(t.companies.c.payload).where(t.companies.c.company_id == cid))
        ).scalar_one()
        raw["group"].update(
            group_id=None,
            visible_company_ids=["demo_hidden"],
            observed_group_revenue_sum="999999",
            feature_refs=["hidden_feature"],
        )
        await conn.execute(
            update(t.companies).where(t.companies.c.company_id == cid).values(payload=raw)
        )
    tools = CompanyTools(ConnectionEngine(conn))
    result = await tools.get_profile(await current_context(conn, "alice"), "demo_a")
    assert result.group.visible_company_ids == ["demo_a"]
    assert result.group.observed_group_revenue_sum is None and result.group.feature_refs == []


async def test_import_failure_rolls_back_all_sources(db, case_csv, tmp_path):
    from asyncpg import UniqueViolationError
    from sqlalchemy.exc import IntegrityError

    from app.ingestion.csv.audit import FILES
    from app.ingestion.csv.importer import IMPORT_VERSION, run

    path = case_csv / FILES["companies"]
    path.write_text(path.read_text().replace("U1;", "demo_a;"))
    with pytest.raises((IntegrityError, UniqueViolationError)):
        await run(case_csv, tmp_path / "failed.json", apply=True, db_engine=db)
    async with db.connect() as conn:
        assert (
            await conn.execute(
                select(func.count())
                .select_from(t.source_versions)
                .where(t.source_versions.c.import_version == IMPORT_VERSION)
            )
        ).scalar_one() == 0


async def test_bulk_payment_payloads_match_reference_dtos(connection, case_csv, monkeypatch):
    import sqlite3

    from app.ingestion.csv import importer
    from app.ingestion.csv.audit import FILES
    from app.ingestion.csv.normalization import normalize_payment

    # Decimal/scientific notation, raw counts and a valid zero must survive COPY.
    with (case_csv / FILES["payments"]).open("a") as stream:
        stream.write("U1;U2;1E+3;2.0\nU2;U2;0.00;0\n")
    monkeypatch.setattr(importer, "COPY_BATCH_ROWS", 2)
    with audited_inputs(case_csv) as (index, report):
        await import_snapshot(connection, index, report)
        index.row_factory = sqlite3.Row
        source = importer.source_versions(report)["payments"]
        for row in index.execute(importer.PAYMENT_ROWS_SQL):
            payment = normalize_payment(dict(row))
            ref = importer.row_ref(source, row, "payments")
            rid = importer.stable_id(
                "relation", source.id, payment.kind, [payment.payer, payment.beneficiary], ref.row
            )
            expected = RelationPath(
                id=rid,
                kind=payment.kind,
                nodes=[payment.payer, payment.beneficiary],
                provenance=[ref],
                quality="verified",
            )
            actual = (
                await connection.execute(
                    select(t.relations.c.payload).where(t.relations.c.id == rid)
                )
            ).scalar_one()
            assert actual == expected.model_dump(mode="json")
            for name, value in (("PAY_AMT", payment.amount), ("PAY_CNT", payment.count)):
                feature = value.to_feature(importer.stable_id("feature", rid, name), name, ref)
                stored = (
                    await connection.execute(
                        select(t.features.c.payload).where(t.features.c.id == feature.id)
                    )
                ).scalar_one()
                assert stored == feature.model_dump(mode="json")


async def test_copied_rows_are_invisible_until_commit_and_rollback_on_failure(
    connection, db, case_csv
):
    observed = False

    async def fail(table, count):
        nonlocal observed
        if table != "features":
            return
        assert count > 0
        async with db.connect() as reader:
            # Four synthetic profiles and their features are the only committed data.
            assert (
                await reader.execute(select(func.count()).select_from(t.source_versions))
            ).scalar_one() == 1
            assert (
                await reader.execute(select(func.count()).select_from(t.companies))
            ).scalar_one() == 4
            assert (
                await reader.execute(select(func.count()).select_from(t.features))
            ).scalar_one() == 9
        observed = True
        raise RuntimeError("fault after COPY")

    with audited_inputs(case_csv) as (index, report):
        with pytest.raises(RuntimeError, match="fault after COPY"):
            async with connection.begin_nested():
                await import_snapshot(connection, index, report, checkpoint=fail)
    assert observed
    assert (
        await connection.execute(select(func.count()).select_from(t.companies))
    ).scalar_one() == 4
