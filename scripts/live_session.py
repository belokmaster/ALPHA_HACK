"""Explicit operator lifecycle; never dispatches a model or prints credentials."""

import argparse
import asyncio
import hashlib
import json
from decimal import Decimal

from sqlalchemy.ext.asyncio import create_async_engine

from app.budgets.service import BudgetLedger
from app.core.config import Settings
from app.llm.session import SessionBudget, account_snapshot


async def main(args):
    settings = Settings()
    if not settings.llm_api_key.get_secret_value():
        raise SystemExit("Configured provider key required")
    if args.command == "observe":
        print(
            json.dumps(
                {
                    **await account_snapshot(settings.llm_base_url, settings.llm_api_key),
                    "base_url": settings.llm_base_url,
                },
                default=str,
            )
        )
        return
    if not settings.llm_live_session_id:
        raise SystemExit("Explicit LLM_LIVE_SESSION_ID required")
    engine = create_async_engine(settings.database_url)
    scope = "key_" + hashlib.sha256(settings.llm_api_key.get_secret_value().encode()).hexdigest()
    session = SessionBudget(
        BudgetLedger(engine),
        scope,
        settings.llm_live_session_id,
        settings.llm_base_url,
        settings.llm_api_key,
    )
    try:
        if args.command == "open":
            if not args.accept_unconfirmed_cap_and_price or args.expected_spend is None or not args.authorization_id:
                raise SystemExit("Explicit authorization ID, risk acceptance and expected baseline required")
            await session.open(
                expected_spend=args.expected_spend,
                max_calls=args.max_calls,
                budget_rub=args.budget_rub,
                historical_external=args.historical_external,
                authorization_id=args.authorization_id,
            )
        elif args.command == "close":
            await session.stop(close=True)
        elif args.command == "resume":
            if not args.accept_unconfirmed_cap_and_price or args.expected_spend is None:
                raise SystemExit("Explicit owner reauthorization and observed spend required")
            await session.resume(args.expected_spend)
        print(json.dumps(await session.status(), default=str))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["observe", "open", "status", "resume", "close"])
    parser.add_argument("--accept-unconfirmed-cap-and-price", action="store_true")
    parser.add_argument("--expected-spend", type=Decimal)
    parser.add_argument("--authorization-id", help="Reference to a NEW explicit owner approval; never auto-generated")
    parser.add_argument("--max-calls", type=int, default=30)
    parser.add_argument("--budget-rub", type=Decimal, default=Decimal("50"))
    parser.add_argument(
        "--historical-external", type=Decimal, default=Decimal("0"),
        help="Deprecated: nonzero historical imports are rejected; existing rows are preserved",
    )
    asyncio.run(main(parser.parse_args()))
