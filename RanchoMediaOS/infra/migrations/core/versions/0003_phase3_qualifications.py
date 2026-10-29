"""Phase 3 platform qualifications and offline website account.

Revision ID: 0003_core
Revises: 0002_core
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_core"
down_revision = "0002_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint("platform_accounts_platform_check", "platform_accounts", type_="check")
    with op.batch_alter_table("platform_accounts") as batch:
        batch.create_check_constraint(
            "ck_platform_accounts_platform", "platform IN ('mock','website','postiz')"
        )
    op.create_table(
        "platform_qualifications",
        sa.Column("adapter", sa.String(50), nullable=False),
        sa.Column("platform", sa.String(30), nullable=False),
        sa.Column("level", sa.String(30), nullable=False),
        sa.Column("qualified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("capabilities_json", sa.JSON(), nullable=False),
        sa.Column("known_limitations_json", sa.JSON(), nullable=False),
        sa.Column("source_commit", sa.String(64), nullable=False),
        sa.Column("test_report", sa.String(300), nullable=False),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("owners.id"), nullable=False),
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner_id", "id"),
        sa.UniqueConstraint("owner_id", "platform"),
        sa.CheckConstraint(
            "level IN ('UNQUALIFIED','QUALIFIED_OFFLINE','QUALIFIED_STAGING','QUALIFIED_LIVE')"
        ),
    )
    op.create_index(
        "ix_platform_qualifications_owner_id",
        "platform_qualifications",
        ["owner_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_platform_qualifications_owner_id", table_name="platform_qualifications")
    op.drop_table("platform_qualifications")
    with op.batch_alter_table("platform_accounts") as batch:
        batch.drop_constraint("ck_platform_accounts_platform", type_="check")
        batch.create_check_constraint("ck_platform_accounts_platform", "platform = 'mock'")
