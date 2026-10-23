"""Separate process; short SQL claim + fenced completion + renewable lease."""

import asyncio
import signal
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import exists, insert, or_, select, update

from app.core import tables as t
from app.core.config import settings
from app.core.db import engine
from app.core.errors import AppError
from app.jobs.runner import run_fixture
from contracts.v1 import AgentRun, APIError, ErrorCode, ErrorEnvelope, ToolCall


async def claim():
    now = datetime.now(UTC)
    async with engine.begin() as c:
        # Unknown provider outcomes are never retried, regardless of remaining attempts.
        unknown = exists(
            select(t.metadata.tables["model_calls"].c.id).where(
                t.metadata.tables["model_calls"].c.job_id == t.jobs.c.id,
                t.metadata.tables["model_calls"].c.outcome.in_(["started", "uncertain"]),
            )
        )
        expired = (t.jobs.c.status == "running") & (t.jobs.c.lease_until < now)
        error = ErrorEnvelope(
            error=APIError(
                code=ErrorCode.backend_unavailable,
                message="Lease exhausted or model outcome unknown; manual reconciliation required",
                request_id="worker",
            )
        ).model_dump(mode="json")
        await c.execute(
            update(t.jobs)
            .where(expired, or_(t.jobs.c.attempt >= settings.max_attempts, unknown))
            .values(status="failed", error=error)
        )
        row = (
            (
                await c.execute(
                    select(t.jobs)
                    .where(
                        or_(t.jobs.c.status == "queued", expired),
                        t.jobs.c.attempt < settings.max_attempts,
                    )
                    .order_by(t.jobs.c.created_at)
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
            )
            .mappings()
            .first()
        )
        if not row:
            return None
        token = str(uuid4())
        return (
            (
                await c.execute(
                    update(t.jobs)
                    .where(t.jobs.c.id == row["id"])
                    .values(
                        status="running",
                        attempt=row["attempt"] + 1,
                        lease_token=token,
                        lease_until=now + timedelta(seconds=settings.lease_seconds),
                    )
                    .returning(t.jobs)
                )
            )
            .mappings()
            .one()
        )


async def heartbeat(job):
    while True:
        await asyncio.sleep(settings.heartbeat_seconds)
        async with engine.begin() as c:
            await c.execute(
                update(t.jobs)
                .where(
                    t.jobs.c.id == job["id"],
                    t.jobs.c.status == "running",
                    t.jobs.c.lease_token == job["lease_token"],
                )
                .values(lease_until=datetime.now(UTC) + timedelta(seconds=settings.lease_seconds))
            )


async def execute(job):
    beat = asyncio.create_task(heartbeat(job))
    try:
        deadline = 60.0
        if job["payload"].get("runner") == "analysis_engine":
            from app.llm.runtime import pinned_runtime

            runtime, _ = pinned_runtime(job["payload"].get("llm_runtime"))
            deadline = runtime.timeouts.run_seconds
        async with asyncio.timeout(deadline):
            controller = None
            if job["payload"].get("runner") == "analysis_engine":
                from app.jobs.analysis import run_analysis

                result, controller = await run_analysis(engine, job)
                budget = controller.budget
            else:
                async with engine.connect() as c:
                    result, budget = await run_fixture(c, job)
        async with engine.begin() as c:
            row = (
                (await c.execute(select(t.jobs).where(t.jobs.c.id == job["id"]).with_for_update()))
                .mappings()
                .one()
            )
            if (
                row["status"] != "running"
                or row["lease_token"] != job["lease_token"]
                or row["lease_until"] < datetime.now(UTC)
            ):
                return
            # Recheck grants at completion as well as at the tool boundary.
            from app.auth.service import authorize, current_context

            live = await current_context(c, job["principal_id"])
            await authorize(c, live, job["company_id"], "analysis.run")
            if live.acl_version != result.context.acl_version:
                raise AppError(ErrorCode.forbidden, "Grants changed", 403)
            if controller:
                from app.agent.providers import pinned

                await pinned(c, result.context)
            run = AgentRun(
                id=result.run_id,
                job_id=job["id"],
                runner="analysis_engine" if controller else "fixture_runner",
                steps_used=budget.transitions_used,
                tool_calls_used=budget.tools_used,
                model_calls_used=budget.models_used,
                checkpoints=controller.checkpoints + ["persisted"]
                if controller
                else [
                    "scope_checked",
                    "temporal_checked",
                    "contract_validated",
                    "persisted",
                ],
            )
            await c.execute(
                insert(t.runs).values(
                    id=run.id, job_id=job["id"], payload=run.model_dump(mode="json")
                )
            )
            for i, name in enumerate(
                controller.tools
                if controller
                else ["get_profile", "get_event", "get_evidence"][: budget.tools_used]
            ):
                tool = ToolCall(
                    id=run.id + str(i),
                    run_id=run.id,
                    tool=name,
                    status="ok",
                    source_ids=result.source_version_ids,
                    reason_code="bounded_read_only" if controller else "fixture_runner",
                )
                await c.execute(
                    insert(t.tool_calls).values(
                        id=tool.id, run_id=run.id, payload=tool.model_dump(mode="json")
                    )
                )
            await c.execute(
                insert(t.analyses).values(
                    id=result.id,
                    job_id=job["id"],
                    principal_id=job["principal_id"],
                    company_id=job["company_id"],
                    payload=result.model_dump(mode="json"),
                )
            )
            if job["kind"] == "followup":
                await c.execute(
                    insert(t.messages).values(
                        id=job["id"],
                        job_id=job["id"],
                        conversation_id=result.conversation_id,
                        payload={
                            "schema_version": "1.0.0",
                            "assumptions": job["payload"]["assumptions"],
                            "text": job["payload"]["request"]["text"],
                        },
                    )
                )
            await c.execute(
                update(t.jobs)
                .where(t.jobs.c.id == job["id"])
                .values(
                    status="partial" if result.status != "ready" else "succeeded",
                    result_id=result.id,
                    lease_until=None,
                )
            )
    except Exception as exc:
        code = exc.code if isinstance(exc, AppError) else ErrorCode.internal
        err = ErrorEnvelope(
            error=APIError(
                code=code,
                message="Analysis execution stopped",
                request_id="worker",
                job_id=job["id"],
            )
        )
        async with engine.begin() as c:
            await c.execute(
                update(t.jobs)
                .where(
                    t.jobs.c.id == job["id"],
                    t.jobs.c.lease_token == job["lease_token"],
                    t.jobs.c.status == "running",
                )
                .values(status="failed", error=err.model_dump(mode="json"))
            )
    finally:
        beat.cancel()
        with suppress(asyncio.CancelledError):
            await beat


async def release_interrupted(job):
    """Fence cancellation recovery; never retry a possibly paid model call."""
    async with engine.begin() as c:
        unknown = (
            await c.execute(
                select(t.metadata.tables["model_calls"].c.id)
                .where(
                    t.metadata.tables["model_calls"].c.job_id == job["id"],
                    t.metadata.tables["model_calls"].c.outcome.in_(["started", "uncertain"]),
                )
                .limit(1)
            )
        ).first() is not None
        retryable = not unknown and job["attempt"] < settings.max_attempts
        error = ErrorEnvelope(
            error=APIError(
                code=ErrorCode.backend_unavailable,
                message="Worker interrupted; reconciliation required",
                request_id="worker",
                job_id=job["id"],
            )
        ).model_dump(mode="json")
        await c.execute(
            update(t.jobs)
            .where(
                t.jobs.c.id == job["id"],
                t.jobs.c.status == "running",
                t.jobs.c.lease_token == job["lease_token"],
            )
            .values(
                status="queued" if retryable else "failed",
                lease_until=None,
                lease_token=None,
                error=None if retryable else error,
            )
        )


async def main(*, shutdown_grace_seconds=5.0):
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop.set)
    try:
        while not stop.is_set():
            job = await claim()
            if not job:
                with suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), 0.25)
                continue
            if stop.is_set():
                await release_interrupted(job)
                break
            task = asyncio.create_task(execute(job))
            stopping = asyncio.create_task(stop.wait())
            try:
                await asyncio.wait((task, stopping), return_when=asyncio.FIRST_COMPLETED)
                if not task.done():
                    try:
                        await asyncio.wait_for(asyncio.shield(task), shutdown_grace_seconds)
                    except TimeoutError:
                        task.cancel()
                        with suppress(asyncio.CancelledError):
                            await task
                        await release_interrupted(job)
                else:
                    await task
            finally:
                stopping.cancel()
                with suppress(asyncio.CancelledError):
                    await stopping
    finally:
        await engine.dispose()
        for signum in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(signum)


if __name__ == "__main__":
    asyncio.run(main())
