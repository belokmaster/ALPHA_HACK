"""Allowlisted metadata only; never log prompts, provider bodies or reasoning."""

import json
import logging

logging.basicConfig(level=logging.INFO, format="%(message)s")


def emit(event: str, **fields: object) -> None:
    logging.getLogger("alfa.llm").info(json.dumps({"event": event, **fields}, default=str))
