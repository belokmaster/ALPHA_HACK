"""Explicit opt-in paid smoke through the same shared gateway. Never run by CI/bootstrap."""

import argparse
import asyncio
import json
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.core.errors import AppError
from app.llm.factory import create_gateway
from contracts.v1 import ErrorCode, LLMRequest


class SmokeReply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: Literal["ok"]


async def smoke(stage: str, expected_model: str | None = None):
    settings = Settings()
    engine = create_async_engine(settings.database_url)
    try:
        gateway = create_gateway(engine, settings)
        selected = gateway.runtime.stage(stage)
        if expected_model is not None and expected_model != selected.model_id:
            raise AppError(ErrorCode.validation, "Select stage model in LLM runtime config", 422)
        run_id, call_id = "smoke_" + uuid4().hex, uuid4().hex
        await gateway.start_run(run_id)
        result = await gateway.complete(
            LLMRequest(
                run_id=run_id,
                call_id=call_id,
                model_id=selected.model_id,
                purpose=stage,
                prompt='Return {"ok":"ok"}. No other content.',
                max_output_tokens=min(64, selected.max_output_tokens),
            ),
            SmokeReply,
        )
        print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False))
    except AppError as exc:
        print(
            json.dumps(
                {
                    "error": exc.code,
                    "message": exc.message,
                    "call_id": getattr(exc, "call_id", None),
                    "model_id": getattr(exc, "model_id", None),
                    "retryable": False,
                }
            )
        )
        raise SystemExit(1) from None
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-paid", action="store_true", help="Explicitly authorize this paid call"
    )
    parser.add_argument("--stage", choices=["plan", "draft", "verify"], default="draft")
    parser.add_argument("--model", help="Legacy assertion only: must match configured stage model")
    args = parser.parse_args()
    if not args.allow_paid:
        parser.error("Paid smoke requires --allow-paid, live opt-in, a key and verified tariffs")
    asyncio.run(smoke(args.stage, args.model))


if __name__ == "__main__":
    main()
