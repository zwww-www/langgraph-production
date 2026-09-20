import os

import pytest
from sqlalchemy import text

from safeops.asyncio_support import configure_loop
from safeops.config import Settings
from safeops.graph.runtime import Runtime
from safeops.tools.local import seed_demo

configure_loop()


@pytest.fixture
async def settings():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("PostgreSQL integration needs TEST_DATABASE_URL; see README")
    if not url.rsplit("/", 1)[-1].endswith("_test"):
        raise ValueError("tests only reset a database whose name ends in _test")
    config = Settings(database_url=url, environment="test", llm_provider="offline")
    async with Runtime(config) as runtime:
        async with runtime.db.engine.begin() as connection:
            tables = await connection.scalars(
                text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
            )
            names = [t for t in tables if t not in ("alembic_version", "checkpoint_migrations")]
            if names:
                await connection.execute(
                    text(
                        "TRUNCATE "
                        + ", ".join('"' + t.replace('"', '""') + '"' for t in names)
                        + " RESTART IDENTITY CASCADE"
                    )
                )
        await seed_demo(runtime.db)
    return config


@pytest.fixture
async def runtime(settings):
    async with Runtime(settings) as instance:
        yield instance
