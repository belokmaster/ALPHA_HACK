import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.core import tables as t
from app.core.errors import AppError
from contracts.v1 import ErrorCode, Job


def job_dto(row):
    return Job(
        id=row["id"],
        kind=row["kind"],
        status=row["status"],
        attempt=row["attempt"],
        result_id=row["result_id"],
        conversation_id=row["payload"].get("conversation_id"),
        error=row["error"],
        poll_url="/api/v1/jobs/" + row["id"],
    )


async def replay(c, ctx, route, key, body):
    if not key or len(key) > 128:
        raise AppError(ErrorCode.validation, "Idempotency-Key required (1..128 chars)", 422)
    h = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    row = (
        (
            await c.execute(
                select(t.jobs).where(
                    t.jobs.c.principal_id == ctx.principal_id,
                    t.jobs.c.route == route,
                    t.jobs.c.idempotency_key == key,
                )
            )
        )
        .mappings()
        .first()
    )
    if row and row["payload_hash"] != h:
        raise AppError(ErrorCode.idempotency_conflict, "Key already used with different body", 409)
    return row, h


async def enqueue(c, ctx, route, key, body, payload, kind, body_hash):
    jid = str(uuid4())
    await c.execute(
        insert(t.jobs)
        .values(
            id=jid,
            principal_id=ctx.principal_id,
            company_id=ctx.company_id,
            route=route,
            idempotency_key=key,
            payload_hash=body_hash,
            kind=kind,
            status="queued",
            created_at=datetime.now(UTC),
            payload=payload,
        )
        .on_conflict_do_nothing(index_elements=["principal_id", "route", "idempotency_key"])
    )
    row, _ = await replay(c, ctx, route, key, body)
    return job_dto(row)
