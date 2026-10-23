"""Restore committed test state, including writes made by API/worker connections.

Never usable on the demo/working/shared provider ledger: requires an init-time
disposable database marker, controlled transport, no live/key, and a quiescent DB.
Only known, settled dummy-key calls may be removed. Unknown calls fail closed.
"""

import asyncio
import hashlib
from contextlib import asynccontextmanager

from sqlalchemy import and_, delete, select, text
from sqlalchemy.dialects.postgresql import insert

from app.core import tables as t
from app.core.config import Settings

MARKER = "g1-disposable-offline-scenarios-v1"
SCOPE = "key_" + hashlib.sha256(b"controlled-offline-key-not-a-provider-secret").hexdigest()
LEDGER_TABLES = {
    "budget_days",
    "reservations",
    "model_calls",
    "llm_runs",
    "llm_key_gates",
    "llm_attempts",
}


async def snapshot(c):
    return {
        table.name: {
            tuple(row[col.name] for col in table.primary_key): dict(row)
            for row in (await c.execute(select(table))).mappings()
        }
        for table in t.metadata.sorted_tables
    }


async def guard(c, db):
    cfg = Settings()
    if (
        db.url.database != "alfa_test"
        or cfg.analysis_mode != "controlled"
        or not cfg.demo_mode
        or cfg.live_llm_enabled
        or cfg.llm_api_key.get_secret_value()
        or cfg.controlled_llm_response != "ok"
    ):
        raise RuntimeError("Scenario isolation requires disposable offline controlled alfa_test")
    marker = await c.scalar(
        text(
            "SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname=current_database()"
        )
    )
    if marker != MARKER:
        raise RuntimeError(
            "Disposable scenario DB marker missing; do not run cleanup on this database"
        )


@asynccontextmanager
async def isolated_scenario(db):
    async with db.connect() as lock:
        await guard(lock, db)
        if not await lock.scalar(text("SELECT pg_try_advisory_lock(871902321)")):
            raise RuntimeError("Another G1 scenario owns this disposable database")
        try:
            before = await snapshot(lock)
            if any(r["status"] in {"queued", "running"} for r in before["jobs"].values()):
                raise RuntimeError("Scenario database has in-flight jobs; nothing will be cleaned")
            await lock.commit()
            try:
                yield
            finally:
                # A rollback here would only undo this connection's writes. Wait for
                # separate worker commits before taking the after snapshot instead.
                for _ in range(350):
                    async with db.connect() as c:
                        after = await snapshot(c)
                    jobs = {k: r for k, r in after["jobs"].items() if k not in before["jobs"]}
                    if not any(r["status"] in {"queued", "running"} for r in jobs.values()):
                        break
                    await asyncio.sleep(0.2)
                else:
                    raise RuntimeError("Worker not quiescent; test rows retained, no cleanup")
                job_ids = {r["id"] for r in jobs.values()}
                for key, row in after["model_calls"].items():
                    if key not in before["model_calls"] and (
                        row["job_id"] not in job_ids or row["outcome"] != "known"
                    ):
                        raise RuntimeError("Unknown/unowned model outcome: retain ledger and gate")
                for name in LEDGER_TABLES - {"model_calls"}:
                    for key, row in after[name].items():
                        if before[name].get(key) != row and row["scope_id"] != SCOPE:
                            raise RuntimeError("Non-fixture ledger changed: refusing restoration")
                # Reverse FK order removes only newly created rows. Existing sources,
                # analyses and all baseline ledger history remain byte-for-byte state.
                async with db.begin() as c:
                    await guard(c, db)
                    for table in reversed(t.metadata.sorted_tables):
                        for key in after[table.name].keys() - before[table.name].keys():
                            await c.execute(
                                delete(table).where(
                                    and_(
                                        *[
                                            col == value
                                            for col, value in zip(
                                                table.primary_key, key, strict=True
                                            )
                                        ]
                                    )
                                )
                            )
                    for table in t.metadata.sorted_tables:
                        for key, row in before[table.name].items():
                            if after[table.name].get(key) != row:
                                statement = insert(table).values(**row)
                                await c.execute(
                                    statement.on_conflict_do_update(
                                        index_elements=list(table.primary_key),
                                        set_={
                                            col.name: statement.excluded[col.name]
                                            for col in table.columns
                                            if not col.primary_key
                                        },
                                    )
                                )
                async with db.connect() as c:
                    if await snapshot(c) != before:
                        raise RuntimeError("Scenario state differs after restoration")
                print(
                    f"scenario_isolation: restored baseline after {len(job_ids)} worker jobs; no filesystem exports"
                )
        finally:
            await lock.execute(text("SELECT pg_advisory_unlock(871902321)"))
            await lock.commit()
