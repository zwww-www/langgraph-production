from contextlib import asynccontextmanager

import httpx
import pytest

from safeops.config import Settings
from safeops.llm.base import LLMRequest
from safeops.llm.providers import CompatibleProvider
from safeops.tools.adapters import bind_external, http_handler, mcp_handler
from safeops.tools.registry import ToolRegistry


async def test_http_adapter_passes_idempotency_and_scope():
    def response(request):
        assert request.headers["Idempotency-Key"] == "stable-key"
        assert b'"customer_id":"CUS-001"' in request.content
        return httpx.Response(200, json={"operation": "confirmed"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        result = await http_handler(client, "https://fake.invalid/refund")(
            {"amount_cents": 4500}, "stable-key", "CUS-001"
        )
        assert result == {"operation": "confirmed"}


async def test_http_timeout_is_not_a_definite_refusal():
    def timeout(request):
        raise httpx.ReadTimeout("timeout", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
        with pytest.raises(httpx.ReadTimeout):
            await http_handler(client, "https://fake.invalid")({}, "key", "CUS-001")


async def test_mcp_adapter_offline(monkeypatch):
    import safeops.tools.adapters as adapter

    seen = {}

    @asynccontextmanager
    async def transport(url):
        seen["url"] = url
        yield "read", "write", None

    class Session:
        def __init__(self, read, write):
            assert (read, write) == ("read", "write")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            seen["closed"] = True

        async def initialize(self):
            seen["initialized"] = True

        async def call_tool(self, name, arguments):
            seen.update(name=name, arguments=arguments)
            from mcp.types import CallToolResult

            return CallToolResult(content=[], structuredContent={"confirmed": True})

    monkeypatch.setattr(adapter, "streamablehttp_client", transport)
    monkeypatch.setattr(adapter, "ClientSession", Session)
    result = await mcp_handler("https://fake.invalid/mcp", "refund")(
        {"amount_cents": 45}, "key", "CUS-001"
    )
    assert result == {"confirmed": True}
    assert seen["initialized"] and seen["closed"]
    assert seen["arguments"]["idempotency_key"] == "key"
    assert seen["arguments"]["customer_id"] == "CUS-001"


async def test_external_bindings_cannot_expand_allowlist():
    async with httpx.AsyncClient() as client:
        from safeops.domain.models import ToolRefused

        with pytest.raises(ToolRefused):
            bind_external(
                Settings(
                    tool_bindings={
                        "wire_transfer": {"transport": "http", "url": "https://fake.invalid"}
                    }
                ),
                ToolRegistry(),
                client,
            )


async def test_ci_blocks_live_provider(monkeypatch):
    monkeypatch.setenv("CI", "true")
    async with httpx.AsyncClient() as client:
        provider = CompatibleProvider(
            Settings(llm_api_key="fake", llm_model="test", llm_base_url="https://fake.invalid/v1"),
            client,
        )
        with pytest.raises(RuntimeError, match="disabled"):
            await provider.complete(LLMRequest(tag="supervisor", system="s", user="u"))
