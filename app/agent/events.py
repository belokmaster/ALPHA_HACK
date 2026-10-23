"""M04 DB adapter uses the product's authoritative ACL/span/version boundary."""

from app.agent.providers import Events


class DBEventProvider(Events):
    """No raw evidence query that could bypass document ownership or pinned context."""
