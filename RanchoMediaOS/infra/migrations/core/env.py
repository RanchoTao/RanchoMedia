import asyncio
import os
from pathlib import Path

from alembic import context
from rancho_db.database import Database
from rancho_db.models import Base, MockBase

config = context.config
role = config.get_main_option("database_role", "core")
target_metadata = Base.metadata if role == "core" else MockBase.metadata
environment_url = os.environ.get("DATABASE_URL" if role == "core" else "MOCK_DATABASE_URL")
url = (
    config.attributes.get("database_url")
    or environment_url
    or config.get_main_option("sqlalchemy.url")
)
Path(".runtime").mkdir(exist_ok=True)


def migrate(connection):
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def online():
    database = Database(url)
    async with database.engine.connect() as connection:
        await connection.run_sync(migrate)
    await database.close()


if context.is_offline_mode():
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(online())
