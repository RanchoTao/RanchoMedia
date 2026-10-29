import asyncio
from datetime import timedelta
from io import StringIO
from uuid import UUID, uuid4

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from rancho_core.domain import FrozenClock
from rancho_core.settings import Settings
from rancho_db.database import Database
from rancho_db.models import Base, MetricSnapshot, MockBase
from rancho_platform_sdk.contracts import MetricValues
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from tests.conftest import Runtime
from tests.helpers import approved, post, publication_request


async def test_stored_zero_null_and_counter_reset(runtime: Runtime, clock: FrozenClock) -> None:
    variant = await approved(runtime.client, clock)
    job = await post(runtime.client, "/publications", publication_request(variant, clock))
    publication = await runtime.service.persisted_publication(UUID(job["id"]))
    empty = await runtime.service.analytics.publication(publication.id)
    assert empty.snapshot_id is None and empty.raw is None
    for age, values in (
        (0, MetricValues(views=0, likes=0, comments=0, shares=0, saves=0)),
        (3600, MetricValues(views=100, likes=None, comments=1, shares=1, saves=1)),
        (21600, MetricValues(views=50, likes=5, comments=1, shares=1, saves=1)),
    ):
        async with runtime.service.work() as work:
            await work.metrics.add(
                MetricSnapshot(
                    id=uuid4(),
                    owner_id=runtime.service.owner,
                    created_at=clock.now(),
                    publication_id=publication.id,
                    captured_at=clock.now(),
                    simulated_at=clock.now() + timedelta(seconds=age),
                    post_age_seconds=age,
                    simulation=True,
                    **values.model_dump(),
                )
            )
        result = await runtime.service.analytics.publication(publication.id)
        assert result.percentile is None
        if age == 0:
            assert all(value is None for value in result.derived.model_dump().values())
        elif age == 3600:
            assert result.derived.engagement_rate is None and result.derived.like_rate is None
            assert result.derived.comment_rate == 0.01
            assert result.derived.view_velocity == 100 / 3600
            assert result.derived.engagement_velocity is None
        else:
            assert result.derived.view_velocity is None  # negative counter delta isn't growth
            assert result.derived.engagement_rate == 8 / 50
    assert result.raw.watch_time is None


async def test_matched_age_historical_baseline_and_sql_facts(
    runtime: Runtime, clock: FrozenClock
) -> None:
    publications = []
    for index in range(22):
        clock.value += timedelta(minutes=1)
        variant = await approved(
            runtime.client, clock, topic="Research" if index % 2 == 0 else "Tools"
        )
        job = await post(
            runtime.client, "/publications", publication_request(variant, clock, f"cohort-{index}")
        )
        # 20 matched-age records; one unmatched-age record cannot enter baseline.
        age = 6 if index == 20 else 24
        await post(runtime.client, f"/publications/{job['id']}/metrics/mock", {"age_hours": age})
        publications.append(job)
    analytics = (
        await runtime.client.get(f"/analytics/publications/{publications[-1]['id']}")
    ).json()
    assert analytics["baseline_sample_size"] == 20
    assert analytics["baseline_status"] == "AVAILABLE"
    assert analytics["baseline_mean_engagement"] > 0
    assert 0 <= analytics["percentile"] <= 1
    strategy = await post(runtime.client, "/strategy/recompute")
    assert strategy["sample_size"] == 22
    # Independently average persisted reports and compare with SQL topic aggregates.
    values = []
    for pub in publications[::2]:
        report = (await runtime.client.get(f"/analytics/publications/{pub['id']}")).json()
        values.append(report["derived"]["engagement_rate"])
    research = next(
        fact
        for fact in strategy["quantitative_facts"]
        if fact["dimension"] == "topic" and fact["value"] == "Research"
    )
    assert research["sample_size"] == 11 and len(research["snapshot_ids"]) == 11
    assert abs(research["mean_engagement_rate"] - sum(values) / len(values)) < 1e-12
    facts, size, start, end = await runtime.service.analytics.facts(
        clock.now() - timedelta(minutes=1)
    )
    assert size == 21  # as-of cutoff excludes newest observation
    assert all(len(fact.snapshot_ids) == fact.sample_size for fact in facts)
    summary = (await runtime.client.get("/analytics/summary")).json()
    assert summary["publication_count"] == summary["snapshot_count"] == 22


async def test_alembic_metadata_consistency(settings: Settings) -> None:
    for url, base in ((settings.database_url, Base), (settings.mock_database_url, MockBase)):
        database = Database(url)
        async with database.engine.connect() as connection:
            differences = await connection.run_sync(
                lambda sync, metadata=base.metadata: compare_metadata(
                    MigrationContext.configure(sync, opts={"compare_type": True}), metadata
                )
            )
            names = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
        await database.close()
        assert differences == []
        assert set(names) == set(base.metadata.tables) | {"alembic_version"}


def test_postgresql_ddl_and_offline_upgrade() -> None:
    for base in (Base, MockBase):
        for table in base.metadata.sorted_tables:
            sql = str(CreateTable(table).compile(dialect=postgresql.dialect()))
            assert "UUID" in sql and "TIMESTAMP WITH TIME ZONE" in sql
    for configuration in ("alembic.ini", "alembic-mock.ini"):
        output = StringIO()
        config = Config(configuration, output_buffer=output)
        config.attributes["database_url"] = "postgresql+asyncpg://localhost/rancho_phase2"
        command.upgrade(config, "head", sql=True)  # Explicitly offline; never opens a connection.
        sql = output.getvalue()
        assert "CREATE TABLE" in sql and "TIMESTAMP WITH TIME ZONE" in sql
        if configuration == "alembic.ini":
            assert "CREATE TRIGGER state_publication_jobs" in sql
            assert "JSONB" in sql


async def test_migration_downgrade_upgrade_empty_database(settings: Settings) -> None:
    # No runtime data exists; reversible migration test is isolated to temporary test files.
    for configuration, url in (
        ("alembic.ini", settings.database_url),
        ("alembic-mock.ini", settings.mock_database_url),
    ):
        config = Config(configuration)
        config.attributes["database_url"] = url
        await asyncio.to_thread(command.downgrade, config, "base")
        await asyncio.to_thread(command.upgrade, config, "head")


def test_composite_foreign_keys_are_indexed() -> None:
    for table in Base.metadata.tables.values():
        indexed = [tuple(column.name for column in index.columns) for index in table.indexes]
        for constraint in table.foreign_key_constraints:
            names = tuple(column.name for column in constraint.columns)
            assert any(columns[: len(names)] == names for columns in indexed), (table.name, names)
