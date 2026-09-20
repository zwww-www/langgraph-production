import os
from urllib.parse import urlsplit

import httpx

from safeops.config import Settings
from safeops.llm.base import LLMRequest, LLMResponse


class LLMProviderError(RuntimeError):
    """Safe operational error; never include remote bodies or credentials."""


class CompatibleProvider:
    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.settings, self.client = settings, client
        if not settings.llm_api_key.get_secret_value().strip():
            raise LLMProviderError("真实模型需要配置 LLM_API_KEY")
        if not settings.llm_model.strip() or settings.llm_model == "configured-model":
            raise LLMProviderError("真实模型需要配置 LLM_MODEL")
        url = urlsplit(settings.llm_base_url)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise LLMProviderError("请配置正确的 HTTPS LLM_BASE_URL，不要在地址中包含凭据")
        if settings.llm_provider == "aliyun" and not url.hostname.endswith(".aliyuncs.com"):
            raise LLMProviderError("阿里云模式需要使用百炼官方 aliyuncs.com 接口地址")

    async def complete(self, request: LLMRequest) -> LLMResponse:
        if os.getenv("CI") or self.settings.environment == "test":
            raise RuntimeError("live LLM disabled in CI/test")
        payload = {
            "model": self.settings.llm_model,
            "temperature": 0,
            "max_tokens": self.settings.llm_max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
        }
        if self.settings.llm_provider == "aliyun":
            payload["enable_thinking"] = False
        try:
            response = await self.client.post(
                self.settings.llm_base_url.rstrip("/") + "/chat/completions",
                headers={"Authorization": "Bearer " + self.settings.llm_api_key.get_secret_value()},
                json=payload,
                timeout=self.settings.llm_timeout_seconds,
                follow_redirects=False,
            )
        except httpx.HTTPError:
            raise LLMProviderError("模型连接失败或超时，请检查接口地域与网络") from None
        if not response.is_success:
            raise LLMProviderError(
                f"模型接口调用失败（HTTP {response.status_code}），请检查地域、模型权限及额度"
            )
        try:
            body = response.json()
            text = body["choices"][0]["message"]["content"]
            if not isinstance(text, str) or not text.strip():
                raise ValueError("empty content")
            if body["choices"][0].get("finish_reason") not in (None, "stop"):
                raise ValueError("incomplete response")
        except (ValueError, KeyError, IndexError, TypeError):
            raise LLMProviderError("模型返回格式无效或输出不完整") from None
        return LLMResponse(
            text=text,
            model=body.get("model", self.settings.llm_model),
            usage={k: v for k, v in body.get("usage", {}).items() if isinstance(v, int)},
        )
