from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from rancho_core.domain import DomainError
from rancho_core.schemas import (
    AnalyticsView,
    DerivedMetrics,
    PerformanceFact,
    SummaryView,
)
from rancho_platform_sdk.contracts import MetricValues
from sqlalchemy import Float, case, cast, func, literal, select
from sqlalchemy.sql import Select

from rancho_db.database import Database
from rancho_db.models import ContentGenome, ContentVariant, MetricSnapshot, Publication


def sql_rate(numerator: Any, denominator: Any) -> Any:
    return cast(numerator, Float) / case((denominator > 0, denominator), else_=None)


def engagement() -> Any:
    return (
        MetricSnapshot.likes
        + MetricSnapshot.comments
        + MetricSnapshot.shares
        + MetricSnapshot.saves
    )


class AnalyticsRepository:
    def __init__(self, database: Database, owner: UUID) -> None:
        self.database, self.owner = database, owner

    def series(self) -> Select[Any]:
        previous_views = func.lag(MetricSnapshot.views).over(
            partition_by=MetricSnapshot.publication_id, order_by=MetricSnapshot.post_age_seconds
        )
        previous_engagement = func.lag(engagement()).over(
            partition_by=MetricSnapshot.publication_id, order_by=MetricSnapshot.post_age_seconds
        )
        previous_age = func.lag(MetricSnapshot.post_age_seconds).over(
            partition_by=MetricSnapshot.publication_id, order_by=MetricSnapshot.post_age_seconds
        )
        return (
            select(
                MetricSnapshot.id.label("snapshot_id"),
                MetricSnapshot.publication_id,
                MetricSnapshot.captured_at,
                MetricSnapshot.post_age_seconds,
                Publication.account_id,
                Publication.platform,
                ContentGenome.topic,
                ContentGenome.hook_type,
                ContentGenome.format,
                *[getattr(MetricSnapshot, name).label(name) for name in MetricValues.model_fields],
                sql_rate(engagement(), MetricSnapshot.views).label("engagement_rate"),
                *[
                    sql_rate(getattr(MetricSnapshot, raw), MetricSnapshot.views).label(derived)
                    for raw, derived in (
                        ("likes", "like_rate"),
                        ("comments", "comment_rate"),
                        ("shares", "share_rate"),
                        ("saves", "save_rate"),
                        ("followers_gained", "follow_conversion"),
                    )
                ],
                previous_views.label("previous_views"),
                previous_engagement.label("previous_engagement"),
                previous_age.label("previous_age"),
                engagement().label("engagement"),
                func.row_number()
                .over(
                    partition_by=MetricSnapshot.publication_id,
                    order_by=MetricSnapshot.post_age_seconds.desc(),
                )
                .label("latest"),
            )
            .join(
                Publication,
                (Publication.id == MetricSnapshot.publication_id)
                & (Publication.owner_id == MetricSnapshot.owner_id),
            )
            .join(
                ContentVariant,
                (ContentVariant.id == Publication.variant_id)
                & (ContentVariant.owner_id == Publication.owner_id),
            )
            .join(
                ContentGenome,
                (ContentGenome.id == ContentVariant.genome_id)
                & (ContentGenome.owner_id == ContentVariant.owner_id),
            )
            .where(MetricSnapshot.owner_id == self.owner)
        )

    async def publication(self, publication_id: UUID) -> AnalyticsView:
        series = self.series().subquery()
        async with self.database.sessions() as session:
            pub = await session.scalar(
                select(Publication).where(
                    Publication.id == publication_id, Publication.owner_id == self.owner
                )
            )
            if pub is None:
                raise DomainError("NOT_FOUND", "Publication", 404)
            row = (
                (
                    await session.execute(
                        select(series).where(
                            series.c.publication_id == publication_id, series.c.latest == 1
                        )
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                return AnalyticsView(
                    publication_id=publication_id,
                    snapshot_id=None,
                    raw=None,
                    derived=DerivedMetrics(**{name: None for name in DerivedMetrics.model_fields}),
                    baseline_status="INSUFFICIENT_BASELINE",
                    baseline_sample_size=0,
                    baseline_mean_engagement=None,
                    percentile=None,
                )
            historical = (
                select(series.c.engagement_rate)
                .where(
                    series.c.publication_id != publication_id,
                    series.c.platform == row["platform"],
                    series.c.account_id == row["account_id"],
                    series.c.format == row["format"],
                    series.c.post_age_seconds == row["post_age_seconds"],
                    series.c.captured_at <= row["captured_at"],
                    series.c.engagement_rate.is_not(None),
                )
                .subquery()
            )
            cohort = (
                (
                    await session.execute(
                        select(
                            func.count().label("n"),
                            func.avg(historical.c.engagement_rate).label("mean"),
                            func.avg(
                                case(
                                    (
                                        historical.c.engagement_rate
                                        <= literal(row["engagement_rate"], Float),
                                        1.0,
                                    ),
                                    else_=0.0,
                                )
                            ).label("percentile"),
                        ).select_from(historical)
                    )
                )
                .mappings()
                .one()
            )
            n = int(cohort["n"])
            age_delta = (
                row["post_age_seconds"] - row["previous_age"]
                if row["previous_age"] is not None
                else None
            )
            derived = {
                name: row[name]
                for name in (
                    "engagement_rate",
                    "like_rate",
                    "comment_rate",
                    "share_rate",
                    "save_rate",
                    "follow_conversion",
                )
            }
            for label, current, previous in (
                ("view_velocity", "views", "previous_views"),
                ("engagement_velocity", "engagement", "previous_engagement"),
            ):
                delta = (
                    row[current] - row[previous]
                    if row[current] is not None and row[previous] is not None
                    else None
                )
                derived[label] = (
                    delta / age_delta
                    if delta is not None and delta >= 0 and age_delta and age_delta > 0
                    else None
                )
            return AnalyticsView(
                publication_id=publication_id,
                snapshot_id=row["snapshot_id"],
                raw=MetricValues(**{name: row[name] for name in MetricValues.model_fields}),
                derived=DerivedMetrics(**derived),
                baseline_sample_size=n,
                baseline_status="AVAILABLE" if n >= 20 else "INSUFFICIENT_BASELINE",
                baseline_mean_engagement=cohort["mean"] if n >= 20 else None,
                percentile=cohort["percentile"]
                if n >= 20 and row["engagement_rate"] is not None
                else None,
            )

    async def summary(self) -> SummaryView:
        series = self.series().subquery()
        async with self.database.sessions() as session:
            publication_count = await session.scalar(
                select(func.count())
                .select_from(Publication)
                .where(Publication.owner_id == self.owner)
            )
            snapshot_count = await session.scalar(
                select(func.count())
                .select_from(MetricSnapshot)
                .where(MetricSnapshot.owner_id == self.owner)
            )
            mean = await session.scalar(
                select(func.avg(series.c.engagement_rate)).where(series.c.latest == 1)
            )
            return SummaryView(
                publication_count=int(publication_count or 0),
                snapshot_count=int(snapshot_count or 0),
                mean_engagement_rate=mean,
            )

    async def facts(
        self, cutoff: datetime
    ) -> tuple[list[PerformanceFact], int, datetime | None, datetime | None]:
        # Filter before ROW_NUMBER so an as-of query cannot see future observations.
        series = self.series().where(MetricSnapshot.captured_at <= cutoff).subquery()
        latest = select(series).where(series.c.latest == 1).subquery()
        dimensions: tuple[Literal["topic", "hook_type", "format"], ...] = (
            "topic",
            "hook_type",
            "format",
        )
        columns = [latest.c.snapshot_id]
        for dimension in dimensions:
            column = getattr(latest.c, dimension)
            columns.extend(
                [
                    column,
                    func.count().over(partition_by=column).label(f"{dimension}_n"),
                    func.avg(latest.c.engagement_rate)
                    .over(partition_by=column)
                    .label(f"{dimension}_mean"),
                ]
            )
        columns.extend(
            [
                func.count().over().label("n"),
                func.min(latest.c.captured_at).over().label("start"),
                func.max(latest.c.captured_at).over().label("end"),
            ]
        )
        # All aggregates and provenance IDs come from a single SQL statement/snapshot.
        async with self.database.sessions() as session:
            rows = (await session.execute(select(*columns))).mappings().all()
        if not rows:
            return [], 0, None, None
        facts: list[PerformanceFact] = []
        for dimension in dimensions:
            groups: dict[str, PerformanceFact] = {}
            for row in rows:
                value = row[dimension]
                if value not in groups:
                    groups[value] = PerformanceFact(
                        dimension=dimension,
                        value=value,
                        sample_size=row[f"{dimension}_n"],
                        mean_engagement_rate=row[f"{dimension}_mean"],
                        snapshot_ids=[],
                    )
                groups[value].snapshot_ids.append(row["snapshot_id"])
            for fact in groups.values():
                fact.snapshot_ids.sort(key=str)
            facts.extend(
                sorted(
                    groups.values(),
                    key=lambda fact: (
                        fact.mean_engagement_rate is None,
                        -(fact.mean_engagement_rate or 0),
                        fact.value,
                    ),
                )
            )
        return facts, int(rows[0]["n"]), rows[0]["start"], rows[0]["end"]
