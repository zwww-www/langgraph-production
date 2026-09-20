from typing import Protocol

from pydantic import Field

from safeops.domain.models import Model, digest


class LLMRequest(Model):
    tag: str
    system: str
    user: str

    def key(self) -> str:
        return digest(self.model_dump())


class LLMResponse(Model):
    text: str
    model: str
    usage: dict[str, int] = Field(default_factory=dict)


class Provider(Protocol):
    async def complete(self, request: LLMRequest) -> LLMResponse: ...
