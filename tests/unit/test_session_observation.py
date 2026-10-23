"""Daily and historical counters have distinct roles; historical imports remain blocked."""

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from app.llm.session import SessionBudget, account_snapshot, account_spend

URL = "https://api.alfahack-gateway26.ru/v1"


async def test_daily_reset_uses_historical_counter_for_session_delta(monkeypatch):
    requests = []

    def respond(request):
        requests.append((request.method, str(request.url)))
        return httpx.Response(200, json={"info": {
            "spend": 0, "total_spend": 1.10600500492482, "max_budget": 500,
        }})

    monkeypatch.setattr(
        "app.llm.session.httpx.AsyncHTTPTransport",
        lambda **kwargs: httpx.MockTransport(respond),
    )
    snapshot = await account_snapshot(URL, SecretStr("offline-test-only"))
    assert snapshot["spend"] == 0
    assert snapshot["total_spend"] == Decimal("1.10600500492482")
    assert snapshot["remaining"] == 500
    assert await account_spend(URL, SecretStr("offline-test-only")) == Decimal("1.10600500492482")
    assert requests == [("GET", URL.removesuffix("/v1") + "/key/info")] * 2


async def test_historical_manual_probes_cannot_be_imported_for_another_key_or_day(monkeypatch):
    async def unexpected_observation(*args):
        pytest.fail("Historical replay must be rejected before network or DB mutation")

    monkeypatch.setattr("app.llm.session.account_spend", unexpected_observation)
    # No connection is needed: the obsolete import must fail before touching a ledger.
    ledger = SimpleNamespace(engine=None, clock=lambda: datetime(2026, 10, 23, tzinfo=UTC))
    session = SessionBudget(ledger, "another-key", "another-session", URL, SecretStr("offline"))
    with pytest.raises(ValueError, match="Historical external import is no longer supported"):
        await session.open(
            expected_spend=Decimal("1.10600500492482"), max_calls=2,
            budget_rub=Decimal("50"), historical_external=Decimal("0.0064385253075"),
        )
