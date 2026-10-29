"""lease and immutable runtime records

Revision ID: 0002_core
Revises: 0001_core
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_core"
down_revision = "0001_core"
branch_labels = None
depends_on = None

IMMUTABLE = (
    "content_genomes",
    "content_items",
    "metric_snapshots",
    "strategy_snapshots",
    "audit_logs",
    "agent_runs",
    "next_topic_candidates",
    "publications",
)
MUTABLE_COLUMNS = {
    "content_variants": ("status",),
    "review_approvals": ("revoked",),
    "publication_jobs": (
        "status",
        "attempt_count",
        "last_error",
        "updated_at",
        "retry_at",
        "lease_expires_at",
    ),
}
STATE_RULES = {
    "publication_jobs": {
        "SCHEDULED": ("DISPATCHING", "CANCELLED"),
        "DISPATCHING": (
            "PUBLISHED",
            "FAILED_RETRYABLE",
            "FAILED_TERMINAL",
            "OUTCOME_UNKNOWN",
            "NEEDS_HUMAN",
        ),
        "FAILED_RETRYABLE": ("DISPATCHING", "CANCELLED"),
        "OUTCOME_UNKNOWN": ("PUBLISHED", "NEEDS_HUMAN"),
    },
    "effect_ledgers": {
        "PREPARED": ("IN_FLIGHT", "FAILED"),
        "IN_FLIGHT": ("CONFIRMED", "UNKNOWN", "FAILED"),
        "FAILED": ("IN_FLIGHT",),
        "UNKNOWN": ("CONFIRMED",),
    },
    "content_variants": {
        "REQUIRES_REVIEW": ("APPROVED", "REJECTED", "SUPERSEDED"),
        "APPROVED": ("REJECTED", "SUPERSEDED"),
        "REJECTED": ("SUPERSEDED",),
    },
}
VARIANT_FIXED = (
    "id",
    "owner_id",
    "created_at",
    "content_id",
    "genome_id",
    "revision",
    "title",
    "text",
)
APPROVAL_FIXED = (
    "id",
    "owner_id",
    "created_at",
    "variant_id",
    "account_id",
    "scheduled_at",
    "payload_hash",
    "actor_id",
)
JOB_FIXED = (
    "id",
    "owner_id",
    "created_at",
    "idempotency_key",
    "platform",
    "account_id",
    "content_variant_id",
    "approval_id",
    "payload_hash",
    "request_payload",
    "scheduled_at",
)


def upgrade() -> None:
    op.add_column("publication_jobs", sa.Column("lease_expires_at", sa.DateTime(timezone=True)))
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        for table in IMMUTABLE:
            for operation in ("UPDATE", "DELETE"):
                op.execute(
                    f"CREATE TRIGGER guard_{table}_{operation.lower()} "
                    f"BEFORE {operation} ON {table} "
                    "BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_RECORD'); END"
                )
        for table, columns in (
            ("content_variants", VARIANT_FIXED),
            ("review_approvals", APPROVAL_FIXED),
            ("publication_jobs", JOB_FIXED),
        ):
            comparison = " OR ".join(f"NEW.{column} IS NOT OLD.{column}" for column in columns)
            op.execute(
                f"CREATE TRIGGER guard_{table}_update BEFORE UPDATE ON {table} WHEN {comparison} "
                "BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_PAYLOAD'); END"
            )
            op.execute(
                f"CREATE TRIGGER guard_{table}_delete BEFORE DELETE ON {table} "
                "BEGIN SELECT RAISE(ABORT, 'IMMUTABLE_PAYLOAD'); END"
            )
        for table, rules in STATE_RULES.items():
            allowed = " OR ".join(
                f"(OLD.status = '{source}' AND NEW.status IN "
                f"({','.join(repr(target) for target in targets)}))"
                for source, targets in rules.items()
            )
            op.execute(
                f"CREATE TRIGGER state_{table} BEFORE UPDATE ON {table} "
                f"WHEN NEW.status IS NOT OLD.status AND NOT ({allowed}) "
                "BEGIN SELECT RAISE(ABORT, 'INVALID_STATE_TRANSITION'); END"
            )
    elif dialect == "postgresql":
        op.execute(
            "CREATE FUNCTION rancho_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ "
            "BEGIN RAISE EXCEPTION 'IMMUTABLE_RECORD'; END $$"
        )
        for table in IMMUTABLE:
            op.execute(
                f"CREATE TRIGGER guard_{table} BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION rancho_immutable()"
            )
        for table, columns in MUTABLE_COLUMNS.items():
            excluded = ",".join(repr(column) for column in columns)
            op.execute(
                f"CREATE FUNCTION guard_{table}() RETURNS trigger LANGUAGE plpgsql AS $$ "
                "BEGIN IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'IMMUTABLE_PAYLOAD'; END IF; "
                f"IF (to_jsonb(NEW) - ARRAY[{excluded}]) IS DISTINCT FROM "
                f"(to_jsonb(OLD) - ARRAY[{excluded}]) THEN RAISE EXCEPTION 'IMMUTABLE_PAYLOAD'; "
                "END IF; RETURN NEW; END $$"
            )
            op.execute(
                f"CREATE TRIGGER guard_{table} BEFORE UPDATE OR DELETE ON {table} "
                f"FOR EACH ROW EXECUTE FUNCTION guard_{table}()"
            )
        for table, rules in STATE_RULES.items():
            allowed = " OR ".join(
                f"(OLD.status = '{source}' AND NEW.status IN "
                f"({','.join(repr(target) for target in targets)}))"
                for source, targets in rules.items()
            )
            op.execute(
                f"CREATE FUNCTION state_{table}() RETURNS trigger LANGUAGE plpgsql AS $$ "
                f"BEGIN IF NEW.status IS DISTINCT FROM OLD.status AND NOT ({allowed}) THEN "
                "RAISE EXCEPTION 'INVALID_STATE_TRANSITION'; END IF; RETURN NEW; END $$"
            )
            op.execute(
                f"CREATE TRIGGER state_{table} BEFORE UPDATE ON {table} "
                f"FOR EACH ROW EXECUTE FUNCTION state_{table}()"
            )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    for table in STATE_RULES:
        op.execute(
            f"DROP TRIGGER state_{table}" + (f" ON {table}" if dialect == "postgresql" else "")
        )
        if dialect == "postgresql":
            op.execute(f"DROP FUNCTION state_{table}()")
    for table in (*IMMUTABLE, *MUTABLE_COLUMNS):
        if dialect == "sqlite":
            for operation in ("update", "delete"):
                op.execute(f"DROP TRIGGER guard_{table}_{operation}")
        else:
            op.execute(f"DROP TRIGGER guard_{table} ON {table}")
            if table in MUTABLE_COLUMNS:
                op.execute(f"DROP FUNCTION guard_{table}()")
    if dialect == "postgresql":
        op.execute("DROP FUNCTION rancho_immutable()")
    op.drop_column("publication_jobs", "lease_expires_at")
