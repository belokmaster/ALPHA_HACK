"""Follow-up validates a pinned snapshot; text generation belongs to the gateway."""

from app.core.errors import AppError
from contracts.v1 import ErrorCode


def assert_followup_context(parent, context):
    fields = ("id", "version", "principal_id", "company_id", "role", "acl_version", "preferences")
    if any(getattr(parent.context, k) != getattr(context, k) for k in fields):
        raise AppError(ErrorCode.context_stale, "Follow-up context differs from snapshot", 409)
