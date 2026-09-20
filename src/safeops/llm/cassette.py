import asyncio
import json
from pathlib import Path
from typing import Literal

from safeops.llm.base import LLMRequest, LLMResponse, Provider


class CassetteMiss(KeyError):
    pass


class CassetteProvider:
    def __init__(
        self,
        path: Path,
        mode: Literal["record", "replay", "strict_replay"],
        inner: Provider | None = None,
    ):
        self.path, self.mode, self.inner = path, mode, inner
        self.lock = asyncio.Lock()

    async def complete(self, request: LLMRequest) -> LLMResponse:
        async with self.lock:
            entries = await asyncio.to_thread(self._read)
            key = request.key()
            if key in entries:
                return LLMResponse.model_validate(entries[key])
            if self.mode != "record" or self.inner is None:
                raise CassetteMiss(f"cassette miss: {request.tag}/{key}")
            response = await self.inner.complete(request)
            entries[key] = response.model_dump()
            await asyncio.to_thread(self._write, entries)
            return response

    def _read(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    def _write(self, entries: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)
