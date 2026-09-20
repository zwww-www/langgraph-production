from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from safeops.config import Settings
from safeops.domain.models import Conflict, digest


def row_dict(row: Any) -> dict[str, Any]:
    return {c.key: getattr(row, c.key) for c in inspect(row).mapper.column_attrs}


class Database:
    def __init__(self, settings: Settings):
        self.engine = create_async_engine(settings.sqlalchemy_url, pool_pre_ping=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def close(self) -> None:
        await self.engine.dispose()

    @asynccontextmanager
    async def run_lock(self, run_id: str) -> AsyncIterator[None]:
        # Transaction advisory lock lives on a dedicated connection for the whole invocation.
        # A killed process releases it; another worker can then recover the checkpoint.
        key = int(digest(run_id)[:15], 16)
        async with self.engine.begin() as conn:
            locked = await conn.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": key})
            if not locked:
                raise Conflict("run is active in another worker")
            yield
