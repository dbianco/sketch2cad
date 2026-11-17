"""Initial schema: user, session, case_file, case_member and the case code counter.

Case codes (``EXP-<year>-<NNNN>``) are numbered per year through the
``case_code_counter(year, last_number)`` table instead of one sequence per year.
A table needs no DDL when the year changes and is concurrency-safe: the generator
(task 001-012) runs, inside the transaction that inserts the case file,

    INSERT INTO case_code_counter (year, last_number) VALUES (:year, 1)
    ON CONFLICT (year) DO UPDATE SET last_number = case_code_counter.last_number + 1
    RETURNING last_number

The upsert takes a row lock on the year, so concurrent creations are serialized and
never obtain the same number; a rolled-back transaction also rolls back its
increment, so numbers stay gapless. ``case_file.code`` is unique as a last guard.

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

user_role = postgresql.ENUM("technician", "reviewer", "admin", name="user_role", create_type=False)
incident_type = postgresql.ENUM(
    "traffic_collision",
    "crime_scene",
    "inspection",
    "other",
    name="incident_type",
    create_type=False,
)
UUID_DEFAULT = sa.text("gen_random_uuid()")


def upgrade() -> None:
    bind = op.get_bind()
    user_role.create(bind)
    incident_type.create(bind)

    op.create_table(
        "user",
        sa.Column("id", sa.Uuid(), server_default=UUID_DEFAULT, nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", user_role, nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user")),
        sa.UniqueConstraint("username", name=op.f("uq_user_username")),
    )

    op.create_table(
        "session",
        sa.Column("id", sa.Uuid(), server_default=UUID_DEFAULT, nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_sha256", sa.Text(), nullable=False),
        sa.Column("csrf_token", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], name=op.f("fk_session_user_id_user"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_session")),
        sa.UniqueConstraint("token_sha256", name=op.f("uq_session_token_sha256")),
    )
    op.create_index(op.f("ix_session_user_id"), "session", ["user_id"])

    op.create_table(
        "case_file",
        sa.Column("id", sa.Uuid(), server_default=UUID_DEFAULT, nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("incident_type", incident_type, nullable=False),
        sa.Column("incident_date", sa.Date(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("declared_location", sa.Text(), nullable=False),
        sa.Column("organization", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'open'"), nullable=False),
        sa.Column("retention_policy_id", sa.Uuid(), nullable=True),
        sa.Column("legal_hold", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["user.id"], name=op.f("fk_case_file_created_by_user")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_case_file")),
        sa.UniqueConstraint("code", name=op.f("uq_case_file_code")),
    )
    op.create_index(op.f("ix_case_file_created_by"), "case_file", ["created_by"])

    op.create_table(
        "case_member",
        sa.Column("case_file_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "permissions",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["case_file_id"],
            ["case_file.id"],
            name=op.f("fk_case_member_case_file_id_case_file"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], name=op.f("fk_case_member_user_id_user"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("case_file_id", "user_id", name=op.f("pk_case_member")),
    )
    op.create_index(op.f("ix_case_member_user_id"), "case_member", ["user_id"])

    op.create_table(
        "case_code_counter",
        sa.Column("year", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("last_number", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint("year", name=op.f("pk_case_code_counter")),
    )


def downgrade() -> None:
    op.drop_table("case_code_counter")
    op.drop_index(op.f("ix_case_member_user_id"), table_name="case_member")
    op.drop_table("case_member")
    op.drop_index(op.f("ix_case_file_created_by"), table_name="case_file")
    op.drop_table("case_file")
    op.drop_index(op.f("ix_session_user_id"), table_name="session")
    op.drop_table("session")
    op.drop_table("user")

    bind = op.get_bind()
    incident_type.drop(bind)
    user_role.drop(bind)
