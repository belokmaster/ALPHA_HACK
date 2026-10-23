import asyncio
import hashlib
import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.core import tables as t
from app.core.db import engine
from app.ingestion.csv.normalization import (
    normalize_flag,
    normalize_payment_amount,
    normalize_revenue,
)
from contracts.v1 import (
    CompanyProfile,
    Document,
    Event,
    Evidence,
    Grant,
    GroupContext,
    RelationPath,
    SourceRef,
    SourceVersion,
    UserContext,
)

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures/synthetic/scenario.json"


async def seed():
    raw = FIXTURE_PATH.read_bytes()
    data = json.loads(raw)
    source = SourceRef(
        source_version_id="synthetic_v1",
        original_filename="scenario.json",
        sha256=hashlib.sha256(raw).hexdigest(),
        row=1,
    )
    sv = SourceVersion(id="synthetic_v1", ref=source, domain="synthetic")
    async with engine.begin() as c:
        existing = (
            await c.execute(
                select(t.source_versions.c.sha256).where(t.source_versions.c.id == sv.id)
            )
        ).scalar_one_or_none()
        if existing is not None and existing != source.sha256:
            raise ValueError("Synthetic source changed: create a new source/import version")

        async def add(table, **values):
            await c.execute(insert(table).values(**values).on_conflict_do_nothing())

        await add(
            t.source_versions,
            id=sv.id,
            sha256=source.sha256,
            import_version="1",
            payload=sv.model_dump(mode="json"),
        )
        for item in data["companies"]:
            cid = item["id"]
            fs = [
                normalize_revenue(item["revenue"]).to_feature(
                    f"{cid}_revenue", "REVENUE_COMPANY", source
                ),
                normalize_flag(item["loan"]).to_feature(
                    f"{cid}_loan", "CL_PRODUCT_LOA_MARK", source
                ),
                normalize_payment_amount("42.50").to_feature(f"{cid}_payment", "PAY_AMT", source),
            ]
            profile = CompanyProfile(
                company_id=cid,
                snapshot_id=cid + "_v1",
                source_version_id=sv.id,
                name="Синтетическая компания " + cid,
                industry="synthetic_manufacturing",
                client_type="CLIENT",
                status="valid",
                features=fs,
                group=GroupContext(group_id="demo_group"),
            )
            await add(
                t.companies,
                id=profile.snapshot_id,
                source_id=sv.id,
                company_id=cid,
                payload=profile.model_dump(mode="json"),
            )
            for f in fs:
                await add(
                    t.features,
                    id=f.id,
                    snapshot_id=profile.snapshot_id,
                    payload=f.model_dump(mode="json"),
                )
            doc = Document(
                id=cid + "_doc",
                source_version_id=sv.id,
                domain="synthetic",
                title="Учебное событие",
                publisher="Synthetic fixture",
                published_at=datetime.fromisoformat(data["available_at"]),
                available_at=datetime.fromisoformat(data["available_at"]),
                text=data["text"],
            )
            ev = Evidence(
                id=cid + "_evidence",
                document_version_id=doc.id,
                source_version_id=sv.id,
                quote=doc.text,
                span="full",
                value_kind="fact",
                quality="verified",
                domain="synthetic",
                available_at=doc.available_at,
            )
            await add(
                t.documents,
                id=doc.id,
                source_id=sv.id,
                company_id=cid,
                payload=doc.model_dump(mode="json"),
            )
            await add(t.evidence, id=ev.id, document_id=doc.id, payload=ev.model_dump(mode="json"))
        for actor, ids in data["actors"].items():
            await add(t.principals, id=actor, acl_version=1)
            gs = [
                Grant(
                    company_id=i,
                    scopes=[
                        "profile.read",
                        "news.read",
                        "analysis.run",
                        "sources.read",
                        "relations.read",
                    ],
                )
                for i in ids
            ]
            for g in gs:
                await add(
                    t.grants,
                    principal_id=actor,
                    company_id=g.company_id,
                    payload=g.model_dump(mode="json"),
                )
            ctx = UserContext(
                id=actor + "_context",
                principal_id=actor,
                company_id=ids[0],
                version=1,
                acl_version=1,
                grants=gs,
            )
            await add(
                t.contexts,
                id=ctx.id,
                principal_id=actor,
                version=1,
                payload=ctx.model_dump(mode="json"),
            )
        event = Event(
            id="rate_hold",
            source_version_id=sv.id,
            domain="synthetic",
            title="Учебное сохранение ставки",
            available_at=datetime.fromisoformat(data["available_at"]),
            event_at=datetime.fromisoformat(data["available_at"]),
            evidence_ids=[],
        )
        await add(t.events, id=event.id, source_id=sv.id, payload=event.model_dump(mode="json"))
        for kind, nodes in [
            ("group", ["demo_a", "demo_hidden"]),
            ("shared_person", ["demo_a", "synthetic_f", "demo_hidden"]),
            ("observed_payment", ["demo_a", "unknown_u"]),
            ("self_transfer", ["demo_a", "demo_a"]),
        ]:
            rel = RelationPath(
                id=kind, kind=kind, nodes=nodes, provenance=[source], quality="verified"
            )
            await add(
                t.relations,
                id=kind,
                kind=kind,
                source_id=sv.id,
                payload=rel.model_dump(mode="json"),
            )
        from contracts.v1 import DataQualityIssue

        issue = DataQualityIssue(
            id="conflict_u",
            source=source,
            code="conflicting_company_id",
            severity="error",
            safe_behavior="quarantine; no last-row-wins",
        )
        await add(
            t.quality_issues, id=issue.id, source_id=sv.id, payload=issue.model_dump(mode="json")
        )
        conflict = CompanyProfile(
            company_id="conflict_u",
            snapshot_id="conflict_u_v1",
            source_version_id=sv.id,
            name="Quarantined synthetic profile",
            industry=None,
            client_type="unknown",
            status="quarantined",
            features=[],
            warnings=["conflicting_company_id"],
        )
        await add(
            t.companies,
            id=conflict.snapshot_id,
            company_id=conflict.company_id,
            source_id=sv.id,
            payload=conflict.model_dump(mode="json"),
        )
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
