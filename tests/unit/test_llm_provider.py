import json

import httpx
import pytest

from safeops.config import Settings
from safeops.llm.base import LLMRequest
from safeops.llm.providers import CompatibleProvider, LLMProviderError


def configuration(**overrides):
    return Settings(
        **{
            "_env_file": None,
            "llm_provider": "aliyun",
            "llm_api_key": "fake-secret",
            "llm_model": "qwen3.8-flash",
            "llm_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            **overrides,
        }
    )


async def test_aliyun_request_contract(monkeypatch):
    monkeypatch.delenv("CI", raising=False)

    def respond(request):
        assert str(request.url).endswith("/compatible-mode/v1/chat/completions")
        assert request.headers["authorization"] == "Bearer fake-secret"
        body = json.loads(request.content)
        assert body["model"] == "qwen3.8-flash"
        assert body["enable_thinking"] is False
        assert body["response_format"] == {"type": "json_object"}
        assert body["max_tokens"] == 2048
        return httpx.Response(
            200,
            json={
                "model": "qwen3.8-flash",
                "choices": [
                    {"finish_reason": "stop", "message": {"content": '{"route":"billing"}'}}
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 7},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await CompatibleProvider(configuration(), client).complete(
            LLMRequest(tag="supervisor", system="Return JSON", user="查发票")
        )
        assert result.model == "qwen3.8-flash"
        assert result.usage["prompt_tokens"] == 12


@pytest.mark.parametrize(
    "overrides",
    [
        {"llm_api_key": ""},
        {"llm_model": "configured-model"},
        {"llm_base_url": ""},
        {"llm_base_url": "https://untrusted.invalid/v1"},
    ],
)
async def test_missing_or_invalid_live_configuration_fails(overrides):
    async with httpx.AsyncClient() as client:
        with pytest.raises(LLMProviderError):
            CompatibleProvider(configuration(**overrides), client)


@pytest.mark.parametrize("status", [401, 429, 500, 302])
async def test_remote_errors_do_not_leak_or_fallback(monkeypatch, status):
    monkeypatch.delenv("CI", raising=False)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status, text="fake-secret remote body")
        )
    ) as client:
        with pytest.raises(LLMProviderError) as caught:
            await CompatibleProvider(configuration(), client).complete(
                LLMRequest(tag="supervisor", system="JSON", user="test")
            )
        assert str(status) in str(caught.value)
        assert "fake-secret" not in str(caught.value)


async def test_test_environment_blocks_live_even_with_key():
    async with httpx.AsyncClient() as client:
        with pytest.raises(RuntimeError, match="disabled"):
            await CompatibleProvider(configuration(environment="test"), client).complete(
                LLMRequest(tag="supervisor", system="JSON", user="test")
            )
