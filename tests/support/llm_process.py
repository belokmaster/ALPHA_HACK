"""Child used only by offline PostgreSQL process-concurrency tests."""

import asyncio
import os
import sys
from decimal import Decimal
from uuid import uuid4

import httpx
from pydantic import BaseModel, SecretStr
from sqlalchemy.ext.asyncio import create_async_engine

from app.budgets.service import BudgetLedger, Tariff
from app.llm.config import LLMConfig
from app.llm.gateway import LLMGateway
from contracts.v1 import LLMRequest


class Reply(BaseModel):
    answer: int


async def main():
    url = os.environ["TEST_DATABASE_URL"]
    assert url.rsplit("/", 1)[-1] == "alfa_test"
    model = "deepseek/deepseek-v4.1-flash"

    async def handler(_):
        print("transport-entered", flush=True)
        await asyncio.Event().wait()

    engine = create_async_engine(url)
    gateway = LLMGateway(
        BudgetLedger(engine),
        config=LLMConfig(live_enabled=True, billable_output_cap_confirmed=True),
        api_key=SecretStr(sys.argv[1]),
        tariffs={model: Tariff(model, "offline-test", Decimal("1"), Decimal("2"), True)},
        transport=httpx.MockTransport(handler),
    )
    try:
        await gateway.start_run(sys.argv[2])
        await gateway.complete(
            LLMRequest(
                run_id=sys.argv[2],
                call_id=uuid4().hex,
                model_id=model,
                prompt="offline only",
                max_output_tokens=100,
            ),
            Reply,
        )
    finally:
        await engine.dispose()


asyncio.run(main())
