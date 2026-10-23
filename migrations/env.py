import asyncio

from alembic import context

from app.core.db import engine
from app.core.tables import metadata
from app.ingestion.news import tables as news_tables  # noqa: F401 - register news metadata


def configure(connection):
    context.configure(connection=connection, target_metadata=metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run():
    async with engine.connect() as connection:
        await connection.run_sync(configure)
    await engine.dispose()


asyncio.run(run())
