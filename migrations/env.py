import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from safeops.asyncio_support import configure_loop
from safeops.config import Settings
from safeops.persistence.models import Base


def configure(connection):
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        compare_type=True,
        include_object=lambda obj, name, kind, reflected, compare_to: (
            not (kind == "table" and name.startswith("checkpoint"))
        ),
    )
    with context.begin_transaction():
        context.run_migrations()


async def migrate():
    engine = create_async_engine(Settings().sqlalchemy_url)
    async with engine.connect() as connection:
        await connection.run_sync(configure)
    await engine.dispose()


if context.is_offline_mode():
    context.configure(
        url=Settings().sqlalchemy_url, target_metadata=Base.metadata, literal_binds=True
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    configure_loop()
    asyncio.run(migrate())
