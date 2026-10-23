import hashlib
import hmac
from datetime import UTC, datetime

from fastapi import Request
from sqlalchemy import select

from app.core import tables as t
from app.core.config import settings
from app.core.errors import AppError
from contracts.v1 import CompanyProfile, ErrorCode, Grant, GroupContext, UserContext


def digest(value: str) -> str:
    return hmac.new(
        settings.app_secret.get_secret_value().encode(), value.encode(), hashlib.sha256
    ).hexdigest()


async def current_context(c, principal_id: str, lock=False) -> UserContext:
    q = select(t.contexts).where(t.contexts.c.principal_id == principal_id)
    if lock:
        q = q.with_for_update()
    row = (await c.execute(q)).mappings().one()
    ctx = UserContext.model_validate(row["payload"])
    ctx.grants = [
        Grant.model_validate(v)
        for v in (
            await c.execute(
                select(t.grants.c.payload).where(t.grants.c.principal_id == principal_id)
            )
        ).scalars()
    ]
    ctx.acl_version = (
        await c.execute(select(t.principals.c.acl_version).where(t.principals.c.id == principal_id))
    ).scalar_one()
    return ctx


async def authenticate(c, request: Request) -> UserContext:
    token = request.cookies.get("alfa_session", "")
    row = (
        (
            await c.execute(
                select(t.sessions).where(
                    t.sessions.c.id == digest(token), t.sessions.c.expires_at > datetime.now(UTC)
                )
            )
        )
        .mappings()
        .first()
    )
    if not token or not row:
        raise AppError(ErrorCode.forbidden, "Session required", 401)
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        if not hmac.compare_digest(
            row["csrf_hash"], digest(request.headers.get("X-CSRF-Token", ""))
        ):
            raise AppError(ErrorCode.forbidden, "CSRF token required", 403)
    return await current_context(c, row["principal_id"])


async def authorize(c, ctx: UserContext, company_id: str, scope: str):
    grant = (
        await c.execute(
            select(t.grants.c.payload).where(
                t.grants.c.principal_id == ctx.principal_id, t.grants.c.company_id == company_id
            )
        )
    ).scalar_one_or_none()
    if not grant or scope not in grant["scopes"]:
        raise AppError(ErrorCode.not_found, "Resource not found", 404)
    row = (
        await c.execute(select(t.companies.c.payload).where(t.companies.c.company_id == company_id))
    ).scalar_one_or_none()
    if not row or row["client_type"] != "CLIENT" or row["status"] != "valid":
        raise AppError(ErrorCode.not_found, "Resource not found", 404)
    return CompanyProfile.model_validate(row)


async def visible_profiles(c, ctx, scope):
    """Use current server grants; a caller-provided context is never an ACL cache."""
    rows = (
        await c.execute(
            select(t.companies.c.payload)
            .join(t.grants, t.grants.c.company_id == t.companies.c.company_id)
            .where(
                t.grants.c.principal_id == ctx.principal_id,
                t.grants.c.payload["scopes"].contains([scope]),
                t.companies.c.payload["client_type"].astext == "CLIENT",
                t.companies.c.payload["status"].astext == "valid",
            )
            .order_by(t.companies.c.company_id)
        )
    ).scalars()
    return [CompanyProfile.model_validate(row) for row in rows]


async def profile(c, ctx, company_id):
    result = await authorize(c, ctx, company_id, "profile.read")
    visible = [result.company_id]
    if result.group.group_id is not None:
        visible = [
            p.company_id
            for p in await visible_profiles(c, ctx, "profile.read")
            if p.group.group_id == result.group.group_id
        ]
    # Never expose stored group-wide sums/feature refs computed before ACL filtering.
    result.group = GroupContext(group_id=result.group.group_id, visible_company_ids=visible)
    return result


async def owned(c, table, object_id, ctx):
    row = (
        (
            await c.execute(
                select(table).where(
                    table.c.id == object_id, table.c.principal_id == ctx.principal_id
                )
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise AppError(ErrorCode.not_found, "Resource not found", 404)
    if "company_id" in row:
        await authorize(c, ctx, row["company_id"], "analysis.run")
    return row
