import asyncio
import json
from pathlib import Path

import httpx
import pytest
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.core.errors import AppError
from app.llm.config import ALLOWED_BASE_URLS
from app.llm.factory import create_gateway, load_tariffs
from app.llm.transport import ChatTransport, ProviderUsage, input_bound, messages_for
from contracts.v1 import LLMRequest


class Reply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: int


async def test_configured_reasoning_effort_is_sent_without_extra_model_requests():
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={})

    transport = ChatTransport(httpx.MockTransport(handler))
    await transport.send(key="offline", model_id="deepseek/deepseek-v4.1-flash",
                         messages=[{"role": "user", "content": "fixture"}],
                         max_output_tokens=1000, reasoning_effort="none")
    assert len(sent) == 1 and sent[0]["reasoning"] == {"effort": "none"}
    assert sent[0]["max_tokens"] == 1000


def test_reasoning_is_a_subset_of_completion_never_added_twice():
    usage = ProviderUsage(
        prompt_tokens=100,
        completion_tokens=10,
        total_tokens=110,
        completion_tokens_details={"reasoning_tokens": 6},
    )
    assert usage.completion_tokens == 10


@pytest.mark.parametrize(
    "extra",
    [
        {"reasoning_tokens": 6},
        {"completion_tokens_details": {"reasoning_tokens": 11}},
        {"completion_tokens_details": {"reasoning_tokens": True}},
    ],
)
def test_ambiguous_or_inconsistent_reasoning_cannot_be_settled(extra):
    with pytest.raises(ValidationError):
        ProviderUsage(prompt_tokens=100, completion_tokens=10, **extra)


@pytest.mark.parametrize("base_url", sorted(ALLOWED_BASE_URLS))
async def test_http_transport_on_real_controlled_loopback_server(base_url):
    seen = []

    async def handle(reader, writer):
        header = await reader.readuntil(b"\r\n\r\n")
        length = next(
            int(line.split(b":", 1)[1])
            for line in header.split(b"\r\n")
            if line.lower().startswith(b"content-length:")
        )
        body = await reader.readexactly(length)
        seen.append((header, json.loads(body)))
        payload = b'{"controlled":"response"}'
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
            + str(len(payload)).encode()
            + b"\r\nConnection: close\r\n\r\n"
            + payload
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async with await asyncio.start_server(handle, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]

        class LoopbackOnly(httpx.AsyncBaseTransport):
            def __init__(self):
                self.http = httpx.AsyncHTTPTransport(retries=0)

            async def handle_async_request(self, request):
                assert str(request.url).startswith(base_url + "/")
                request.url = request.url.copy_with(scheme="http", host="127.0.0.1", port=port)
                return await self.http.handle_async_request(request)

            async def aclose(self):
                await self.http.aclose()

        status, body = await ChatTransport(LoopbackOnly(), base_url=base_url).send(
            key="offline-synthetic",
            model_id="deepseek/deepseek-v4.1-flash",
            messages=messages_for("offline", Reply),
            max_output_tokens=64,
        )
    assert status == 200 and json.loads(body) == {"controlled": "response"}
    assert len(seen) == 1
    assert b"POST /v1/chat/completions HTTP/1.1" in seen[0][0]
    assert seen[0][1]["max_tokens"] == 64 and not seen[0][1]["stream"]


async def test_transport_does_not_follow_redirect_or_retry():
    calls = []

    def response(request):
        calls.append(request)
        return httpx.Response(307, headers={"Location": "https://different.invalid"})

    status, _ = await ChatTransport(httpx.MockTransport(response)).send(
        key="offline", model_id="deepseek/deepseek-v4.1-flash", messages=[], max_output_tokens=1
    )
    assert status == 307 and len(calls) == 1


def test_provider_usage_is_strict_and_consistent():
    for data in (
        {"prompt_tokens": True, "completion_tokens": 1},
        {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 2},
        {"prompt_tokens": 1},
        {"prompt_tokens": -1, "completion_tokens": 2},
    ):
        with pytest.raises(ValidationError):
            ProviderUsage.model_validate(data)


def test_schema_and_utf8_bytes_are_included_in_reservation_envelope():
    prompt = "Ж" * 100
    messages = messages_for(prompt, Reply)
    assert input_bound(messages) > len(prompt.encode()) + len(json.dumps(Reply.model_json_schema()))


def test_unknown_tariffs_and_disabled_factory_are_offline(tmp_path):
    path = tmp_path / "tariffs.json"
    path.write_text('{"tariffs":[]}')
    assert load_tariffs(path) == {}
    path.write_text('{"tariffs":[{"model_id":"bad"}]}')
    with pytest.raises(AppError):
        load_tariffs(path)
    with pytest.raises(AppError):
        load_tariffs(Path("missing-test-tariffs.json"))
    gateway = create_gateway(
        create_async_engine("postgresql+asyncpg://x:y@unused/alfa_test"),
        Settings(
            analysis_mode="evidence_only",
            live_llm_enabled=False,
            llm_tariff_file="missing-test-file",
        ),
    )
    assert not gateway.config.live_enabled and not gateway.tariffs


def test_typed_request_rejects_bad_ids_and_unbounded_tokens():
    for value in (0, -1, 4001, True, "100"):
        with pytest.raises(ValidationError):
            LLMRequest(
                run_id="run",
                call_id="call",
                model_id="model",
                prompt="test",
                max_output_tokens=value,
            )


@pytest.mark.parametrize("key", [" offline", "offline ", "off\nline"])
async def test_key_whitespace_cannot_shard_a_provider_budget(key):
    from pydantic import SecretStr

    from app.budgets.service import BudgetLedger
    from app.llm.config import LLMConfig
    from app.llm.gateway import LLMGateway

    gateway = LLMGateway(
        BudgetLedger(None), config=LLMConfig(live_enabled=True, billable_output_cap_confirmed=True), api_key=SecretStr(key)
    )
    with pytest.raises(AppError):
        await gateway.start_run("offline")
