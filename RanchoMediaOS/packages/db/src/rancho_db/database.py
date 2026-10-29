from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


class Database:
    def __init__(self, url: str) -> None:
        if url.startswith("sqlite") and ":memory:" in url:
            raise ValueError("Phase 2 requires a persistent database file")
        if url.startswith("sqlite"):
            from sqlalchemy.engine import make_url

            filename = make_url(url).database
            if filename:
                Path(filename).parent.mkdir(parents=True, exist_ok=True)
        self.engine: AsyncEngine = create_async_engine(url)
        if url.startswith("sqlite"):

            @event.listens_for(self.engine.sync_engine, "connect")
            def sqlite_options(connection: Any, record: Any) -> None:
                cursor = connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA busy_timeout=30000")
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.close()

        self.sessions: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self.engine, expire_on_commit=False
        )

    async def close(self) -> None:
        await self.engine.dispose()
