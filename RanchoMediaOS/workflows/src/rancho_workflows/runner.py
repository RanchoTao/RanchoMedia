from collections.abc import Awaitable, Callable
from typing import Protocol
from uuid import UUID, uuid4

from rancho_core.domain import Clock
from rancho_db.database import Database
from rancho_db.models import WorkflowRun
from rancho_db.repositories import UnitOfWork


class WorkflowRunner(Protocol):
    async def execute[T](
        self, name: str, entity_id: UUID, operation: Callable[[], Awaitable[T]]
    ) -> T: ...


class LocalWorkflowRunner:
    """Persisted orchestration boundary; application jobs/outbox remain authoritative."""

    def __init__(self, database: Database, owner: UUID, clock: Clock) -> None:
        self.database, self.owner, self.clock = database, owner, clock

    async def record(self, name: str, entity_id: UUID, status: str) -> None:
        async with UnitOfWork(self.database, self.owner) as work:
            await work.workflows.insert_once(
                WorkflowRun(
                    id=uuid4(),
                    owner_id=self.owner,
                    created_at=self.clock.now(),
                    name=name,
                    entity_id=entity_id,
                    status="RUNNING",
                    finished_at=None,
                ),
                ["owner_id", "name", "entity_id"],
            )
            row = await work.workflows.find(
                WorkflowRun.name == name, WorkflowRun.entity_id == entity_id
            )
            assert row is not None
            row.status = status
            row.finished_at = self.clock.now() if status != "RUNNING" else None

    async def execute[T](
        self, name: str, entity_id: UUID, operation: Callable[[], Awaitable[T]]
    ) -> T:
        await self.record(name, entity_id, "RUNNING")
        try:
            result = await operation()
        except Exception:
            await self.record(name, entity_id, "FAILED")
            raise
        await self.record(name, entity_id, "COMPLETED")
        return result
