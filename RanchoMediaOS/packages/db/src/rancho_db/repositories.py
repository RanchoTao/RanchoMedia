from __future__ import annotations

import builtins
from datetime import datetime
from types import TracebackType
from typing import cast
from uuid import UUID

from rancho_core.domain import (
    DomainError,
    EffectStatus,
    JobStatus,
    effect_transition,
    transition,
)
from sqlalchemy import Table, func, inspect, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from rancho_db.database import Database
from rancho_db.models import (
    AgentRun,
    AuditLog,
    ContentBrief,
    ContentGenome,
    ContentIdea,
    ContentItem,
    ContentVariant,
    EffectLedger,
    MetricSnapshot,
    NextTopicCandidate,
    OutboxEvent,
    Owned,
    Owner,
    PlatformAccount,
    PlatformQualification,
    Publication,
    PublicationJob,
    ReviewApproval,
    Row,
    StrategySnapshot,
    TrendItem,
    WorkflowRun,
)


class Repository[T: Row]:
    def __init__(self, session: AsyncSession, model: type[T], owner_id: UUID | None) -> None:
        self._session, self.model, self.owner_id = session, model, owner_id

    def scope(self) -> list[ColumnElement[bool]]:
        if issubclass(self.model, Owned):
            if self.owner_id is None:
                raise ValueError("Owned repositories require owner")
            return [self.model.owner_id == self.owner_id]
        return []

    async def get(self, row_id: UUID) -> T:
        item = await self.find(self.model.id == row_id)
        if item is None:
            raise DomainError("NOT_FOUND", self.model.__name__, 404)
        return item

    async def find(self, *conditions: ColumnElement[bool]) -> T | None:
        return cast(
            T | None,
            await self._session.scalar(select(self.model).where(*self.scope(), *conditions)),
        )

    async def lock(self, row_id: UUID) -> T:
        if self.owner_id is not None and self._session.get_bind().dialect.name == "sqlite":
            # SQLite ignores FOR UPDATE. Reserve its writer before reading review/job
            # state, using the mutable owner row rather than an immutable payload row.
            await self._session.execute(
                update(Owner).where(Owner.id == self.owner_id).values(name=Owner.name)
            )
        item = await self._session.scalar(
            select(self.model)
            .where(*self.scope(), self.model.id == row_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if item is None:
            raise DomainError("NOT_FOUND", self.model.__name__, 404)
        return item

    async def list(self, *conditions: ColumnElement[bool]) -> list[T]:
        result = await self._session.scalars(
            select(self.model)
            .where(*self.scope(), *conditions)
            .order_by(self.model.created_at, self.model.id)
        )
        return list(result)

    async def add(self, row: T) -> T:
        self._session.add(row)
        await self._session.flush()
        return row

    async def insert_once(self, row: T, keys: builtins.list[str]) -> bool:
        mapper = inspect(self.model)
        assert mapper is not None
        table = cast(Table, mapper.local_table)
        values = {
            column.key: getattr(row, column.key)
            for column in table.columns
            if getattr(row, column.key) is not None or column.default is None
        }
        dialect = self._session.get_bind().dialect.name
        factory = sqlite_insert if dialect == "sqlite" else pg_insert
        statement = (
            factory(self.model)
            .values(**values)
            .on_conflict_do_nothing(index_elements=keys)
            .returning(self.model.id)
        )
        return (await self._session.scalar(statement)) is not None

    async def count(self) -> int:
        return int(
            await self._session.scalar(
                select(func.count()).select_from(self.model).where(*self.scope())
            )
            or 0
        )


class TrendRepository(Repository[TrendItem]):
    def __init__(self, session: AsyncSession, owner: UUID) -> None:
        super().__init__(session, TrendItem, owner)


class ContentRepository:
    def __init__(self, session: AsyncSession, owner: UUID) -> None:
        self.ideas = Repository(session, ContentIdea, owner)
        self.briefs = Repository(session, ContentBrief, owner)
        self.items = Repository(session, ContentItem, owner)
        self.variants = Repository(session, ContentVariant, owner)
        self.genomes = Repository(session, ContentGenome, owner)
        self.approvals = Repository(session, ReviewApproval, owner)


class PublicationRepository(Repository[PublicationJob]):
    def __init__(self, session: AsyncSession, owner: UUID) -> None:
        super().__init__(session, PublicationJob, owner)
        self.publications = Repository(session, Publication, owner)
        self.effects = Repository(session, EffectLedger, owner)
        self.outbox = Repository(session, OutboxEvent, owner)

    async def change_state(self, job: PublicationJob, target: JobStatus, now: datetime) -> bool:
        previous = JobStatus(job.status)
        transition(previous, target)
        result = await self._session.scalar(
            update(PublicationJob)
            .where(
                PublicationJob.id == job.id, *self.scope(), PublicationJob.status == previous.value
            )
            .values(status=target.value, updated_at=now)
            .returning(PublicationJob.id)
            .execution_options(synchronize_session=False)
        )
        if result is None:
            return False
        await self._session.refresh(job)
        return True

    async def change_effect(
        self, job_id: UUID, target: EffectStatus, external: str | None = None
    ) -> None:
        effect = await self.effects.find(EffectLedger.job_id == job_id)
        if effect is None:
            raise DomainError("MISSING_LEDGER", "No effect ledger")
        previous = EffectStatus(effect.status)
        if previous == target:
            return
        effect_transition(previous, target)
        effect.status, effect.external_id = target.value, external


class MetricsRepository(Repository[MetricSnapshot]):
    def __init__(self, session: AsyncSession, owner: UUID) -> None:
        super().__init__(session, MetricSnapshot, owner)


class StrategyRepository(Repository[StrategySnapshot]):
    def __init__(self, session: AsyncSession, owner: UUID) -> None:
        super().__init__(session, StrategySnapshot, owner)
        self.candidates = Repository(session, NextTopicCandidate, owner)

    async def latest(self) -> StrategySnapshot | None:
        successor = aliased(StrategySnapshot)
        return cast(
            StrategySnapshot | None,
            await self._session.scalar(
                select(StrategySnapshot)
                .where(
                    *self.scope(),
                    ~select(successor.id)
                    .where(
                        successor.owner_id == StrategySnapshot.owner_id,
                        successor.supersedes_id == StrategySnapshot.id,
                    )
                    .exists(),
                )
                .order_by(StrategySnapshot.created_at.desc(), StrategySnapshot.id.desc())
                .limit(1)
            ),
        )


class AuditRepository(Repository[AuditLog]):
    def __init__(self, session: AsyncSession, owner: UUID) -> None:
        super().__init__(session, AuditLog, owner)


class UnitOfWork:
    def __init__(self, database: Database, owner: UUID) -> None:
        self._session = database.sessions()
        self.owner = owner
        self.trends = TrendRepository(self._session, owner)
        self.content = ContentRepository(self._session, owner)
        self.jobs = PublicationRepository(self._session, owner)
        self.metrics = MetricsRepository(self._session, owner)
        self.strategies = StrategyRepository(self._session, owner)
        self.audit = AuditRepository(self._session, owner)
        self.owners = Repository(self._session, Owner, None)
        self.accounts = Repository(self._session, PlatformAccount, owner)
        self.qualifications = Repository(self._session, PlatformQualification, owner)
        self.agents = Repository(self._session, AgentRun, owner)
        self.workflows = Repository(self._session, WorkflowRun, owner)

    async def __aenter__(self) -> UnitOfWork:
        await self._session.begin()
        return self

    async def __aexit__(
        self,
        exception: type[BaseException] | None,
        value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            if exception is None:
                await self._session.commit()
            else:
                await self._session.rollback()
        finally:
            await self._session.close()

    async def serialize_owner(self) -> None:
        # Real row lock on PostgreSQL; an early write lock on SQLite.
        result = await self._session.scalar(
            update(Owner).where(Owner.id == self.owner).values(name=Owner.name).returning(Owner.id)
        )
        if result is None:
            raise DomainError("NOT_FOUND", "Owner", 404)
