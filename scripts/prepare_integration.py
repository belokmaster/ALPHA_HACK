"""Explicit real-input demo preparation. No model calls or ledger writes.

Requires imported bank corpus. Imports selected real profile rows with the existing
normalizer; does NOT mark the full CSV snapshot imported. Demo grants are a separate
explicit flag, never inferred from CLIENT. The default target remains isolated alfa_test;
--shared-demo explicitly opts into the configured existing demo DB without replacing rows.
"""

import argparse
import asyncio
import csv
import hashlib
import json
import os
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import create_async_engine

from app.core import tables as t
from app.core.config import Settings
from app.ingestion.csv.importer import company_profile, stable_id, valid_id
from app.ingestion.pdf.pipeline import document_id, load_corpus_config
from contracts.v1 import Event, Grant, SourceRef, SourceVersion


def selected_profiles(csv_path, companies):
    manifest = json.loads(Path("config/inputs.manifest.json").read_text())
    expected = next(f["sha256"] for f in manifest["files"] if f["path"] == str(csv_path))
    digest = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    if digest != expected:
        raise ValueError("Original company CSV hash differs from the inputs manifest")
    version = "case-profile-subset-1"
    sid = stable_id("csv_subset", version, digest)
    source = SourceVersion(
        id=sid,
        domain="case",
        import_version=version,
        ref=SourceRef(
            source_version_id=sid,
            original_filename=csv_path.name,
            sha256=digest,
            parser_version="csv-audit-1",
        ),
    )
    found = {}
    with csv_path.open(newline="") as stream:
        for number, row in enumerate(csv.DictReader(stream, delimiter=";"), 2):
            cid = row["SYNTHETIC_U"]
            if cid not in companies:
                continue
            if cid in found and {k: v for k, v in found[cid].items() if k != "row_no"} != row:
                raise ValueError(
                    "Conflicting company rows; subset preparation refuses quarantine bypass"
                )
            found.setdefault(cid, {**row, "row_no": number})
    if set(found) != set(companies):
        raise ValueError("Requested company missing")
    profiles = [company_profile(row, source, False) for row in found.values()]
    if any(p.client_type != "CLIENT" or p.status != "valid" for p in profiles):
        raise ValueError("Only valid CLIENT profiles allowed in this explicit demo")
    return source, profiles


def preparation_url(args):
    if args.shared_demo:
        settings = Settings()
        if not settings.demo_mode or not args.apply or not args.demo_grant:
            raise SystemExit("--shared-demo requires DEMO_MODE, --apply and explicit --demo-grant")
        return settings.database_url
    url = os.environ.get("TEST_DATABASE_URL", "")
    if url.rsplit("/", 1)[-1] != "alfa_test":
        raise SystemExit("Explicit isolated TEST_DATABASE_URL ending /alfa_test required")
    return url


async def prepare(args):
    url = preparation_url(args)
    source, profiles = selected_profiles(Path("raw/csv/df_Uid_data_hack.csv"), args.company)
    cfg = json.loads(Path("config/integration-case.json").read_text())
    corpus = load_corpus_config()
    doc_id = document_id(corpus["documents"][cfg["filename"]], corpus)
    eid = f"ev_{doc_id}_{cfg['event_span']}"
    db = create_async_engine(url)
    try:
        async with db.begin() as c:
            doc = (
                (await c.execute(select(t.documents).where(t.documents.c.id == doc_id)))
                .mappings()
                .one()
            )
            fragment = (
                await c.execute(select(t.evidence.c.payload).where(t.evidence.c.id == eid))
            ).scalar_one()
            assert fragment["document_version_id"] == doc_id and fragment["value_kind"] == "fact"
            event = Event(
                **{
                    k: cfg[k]
                    for k in (
                        "id",
                        "domain",
                        "title",
                        "event_at",
                        "event_time_source",
                        "available_at",
                        "topics",
                    )
                },
                source_version_id=doc["source_id"],
                evidence_ids=[eid],
            )
            if args.apply:
                await c.execute(
                    insert(t.source_versions)
                    .values(
                        id=source.id,
                        sha256=source.ref.sha256,
                        import_version=source.import_version,
                        payload=source.model_dump(mode="json"),
                    )
                    .on_conflict_do_nothing()
                )
                for p in profiles:
                    old = (
                        await c.execute(
                            select(t.companies.c.payload).where(
                                t.companies.c.company_id == p.company_id
                            )
                        )
                    ).scalar_one_or_none()
                    if old is not None and old != p.model_dump(mode="json"):
                        raise ValueError("Existing profile differs; never replace working data")
                    await c.execute(
                        insert(t.companies)
                        .values(
                            id=p.snapshot_id,
                            company_id=p.company_id,
                            source_id=source.id,
                            payload=p.model_dump(mode="json"),
                        )
                        .on_conflict_do_nothing()
                    )
                    for f in p.features:
                        await c.execute(
                            insert(t.features)
                            .values(
                                id=f.id,
                                snapshot_id=p.snapshot_id,
                                payload=f.model_dump(mode="json"),
                            )
                            .on_conflict_do_nothing()
                        )
                    if args.demo_grant:
                        grant = Grant(
                            company_id=p.company_id,
                            scopes=["profile.read", "sources.read", "analysis.run", "news.read"],
                        )
                        await c.execute(
                            insert(t.grants)
                            .values(
                                principal_id=args.demo_grant,
                                company_id=p.company_id,
                                payload=grant.model_dump(mode="json"),
                            )
                            .on_conflict_do_nothing()
                        )
                old = (
                    await c.execute(select(t.events.c.payload).where(t.events.c.id == event.id))
                ).scalar_one_or_none()
                if old is not None and old != event.model_dump(mode="json"):
                    raise ValueError("Immutable case event collision")
                await c.execute(
                    insert(t.events)
                    .values(
                        id=event.id,
                        source_id=event.source_version_id,
                        payload=event.model_dump(mode="json"),
                    )
                    .on_conflict_do_nothing()
                )
        result = {
            "applied": args.apply,
            "companies": [p.company_id for p in profiles],
            "event": event.model_dump(mode="json"),
            "profile_source": source.model_dump(mode="json"),
            "limitations": cfg["limitations"],
            "full_csv_import": False,
            "demo_grant": args.demo_grant if args.apply else None,
            "target": "configured_shared_demo" if args.shared_demo else "isolated_test",
        }
        out = Path("artifacts/integration/preparation.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps(result, ensure_ascii=False))
    finally:
        await db.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company", action="append", required=True, type=valid_id)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--demo-grant", choices=["alice", "bob"])
    parser.add_argument(
        "--shared-demo", action="store_true",
        help="Explicitly add immutable demo inputs to Settings.DATABASE_URL; never reset ledger",
    )
    asyncio.run(prepare(parser.parse_args()))


if __name__ == "__main__":
    main()
