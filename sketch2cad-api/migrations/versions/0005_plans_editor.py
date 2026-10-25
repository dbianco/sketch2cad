"""Plans editor (H2): plan, calibration, revision, entity, catalog and artifact.

``plan.status``, ``plan.source_mode`` and ``plan.metric_status`` are PostgreSQL enums;
the other closed value sets are text columns with check constraints, as in
``0002_sources_events``. ``plan`` and ``revision`` reference each other, so the two
foreign keys of ``plan`` towards ``revision`` are added after both tables exist.

Frozen revisions are immutable: the row-level trigger ``revision_frozen_immutable`` runs
``reject_frozen_revision_mutation()`` before every ``UPDATE`` or ``DELETE`` on
``revision`` and raises when ``OLD.state = 'frozen'``. Freezing a draft (one ``UPDATE``
from ``draft`` to ``frozen``) is still allowed. Partial unique indexes keep at most one
active calibration per plan and one active catalog. The ``default-1`` catalog seed is a
separate data migration.

``event.plan_id``, ``event.revision_id`` and ``event.entity_id`` keep no foreign key on
purpose. The event log is append-only and must outlive and never block the rows it
mentions; ``entity_id`` is the generic subject of an event (it already holds
``source.id`` for ``source.*`` kinds); and integrity of the log is proven by the hash
chain, not by referential constraints.

Revision ID: 0005_plans_editor
Revises: 0004_source_raster
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_plans_editor"
down_revision: str | Sequence[str] | None = "0004_source_raster"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

plan_status = postgresql.ENUM(
    "CREATED",
    "CALIBRATION_PENDING",
    "EDITING",
    "REVIEW_PENDING",
    "IN_REVIEW",
    "VALIDATED",
    "EXPORTED",
    "REJECTED",
    "REOPENED",
    name="plan_status",
    create_type=False,
)
source_mode = postgresql.ENUM(
    "scaled_drawing", "schematic_sketch", name="source_mode", create_type=False
)
metric_status = postgresql.ENUM(
    "metric", "approximate", "non_metric", name="metric_status", create_type=False
)
UUID_DEFAULT = sa.text("gen_random_uuid()")
EMPTY_JSONB = sa.text("'{}'::jsonb")
CALIBRATION_METHOD_CHECK = "method IN ('scan_dpi', 'scan_segment', 'photo_homography_4pt', 'none')"
PAPER_SCALE_CHECK = "paper_scale IS NULL OR paper_scale > 0"
REVISION_STATE_CHECK = "state IN ('draft', 'frozen')"
FROZEN_SNAPSHOT_CHECK = (
    "(state = 'draft' AND snapshot IS NULL AND snapshot_sha256 IS NULL AND frozen_at IS NULL)"
    " OR (state = 'frozen' AND snapshot IS NOT NULL AND snapshot_sha256 IS NOT NULL"
    " AND frozen_at IS NOT NULL)"
)
ENTITY_TYPE_CHECK = "type IN ('line', 'polyline', 'point', 'text', 'dimension', 'symbol')"
REVIEW_STATUS_CHECK = "review_status IN ('suggested', 'accepted', 'corrected', 'rejected')"
METRIC_ORIGIN_CHECK = (
    "metric_origin IN ('declared_dimension', 'triangulation', 'baseline_offset', "
    "'scaled_drawing', 'derived', 'trace_only', 'none')"
)
CONFIDENCE_CHECK = "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)"
ARTIFACT_KIND_CHECK = (
    "kind IN ('dxf', 'pdf_plan', 'pdf_audit', 'manifest', 'zip', 'rectified_image')"
)
CREATE_REJECT_FUNCTION = """
CREATE FUNCTION reject_frozen_revision_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.state = 'frozen' THEN
        RAISE EXCEPTION 'revision % is frozen: % is not allowed', OLD.id, TG_OP
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$
"""
CREATE_IMMUTABLE_TRIGGER = """
CREATE TRIGGER revision_frozen_immutable
BEFORE UPDATE OR DELETE ON revision
FOR EACH ROW EXECUTE FUNCTION reject_frozen_revision_mutation()
"""


def created_at() -> sa.Column[sa.DateTime]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def upgrade() -> None:
    bind = op.get_bind()
    plan_status.create(bind)
    source_mode.create(bind)
    metric_status.create(bind)

    op.create_table(
        "plan",
        sa.Column("id", sa.Uuid(), server_default=UUID_DEFAULT, nullable=False),
        sa.Column("case_file_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("primary_source_id", sa.Uuid(), nullable=False),
        sa.Column(
            "source_mode", source_mode, server_default=sa.text("'schematic_sketch'"), nullable=False
        ),
        sa.Column("status", plan_status, server_default=sa.text("'CREATED'"), nullable=False),
        sa.Column(
            "metric_status", metric_status, server_default=sa.text("'non_metric'"), nullable=False
        ),
        sa.Column("current_revision_id", sa.Uuid(), nullable=True),
        sa.Column("validated_revision_id", sa.Uuid(), nullable=True),
        sa.Column("edit_lease", postgresql.JSONB(), nullable=True),
        created_at(),
        sa.ForeignKeyConstraint(
            ["case_file_id"], ["case_file.id"], name=op.f("fk_plan_case_file_id_case_file")
        ),
        sa.ForeignKeyConstraint(
            ["primary_source_id"], ["source.id"], name=op.f("fk_plan_primary_source_id_source")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_plan")),
    )
    op.create_index(op.f("ix_plan_case_file_id"), "plan", ["case_file_id"])
    op.create_index(op.f("ix_plan_primary_source_id"), "plan", ["primary_source_id"])

    op.create_table(
        "revision",
        sa.Column("id", sa.Uuid(), server_default=UUID_DEFAULT, nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("parent_revision_id", sa.Uuid(), nullable=True),
        sa.Column("state", sa.Text(), server_default=sa.text("'draft'"), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), nullable=True),
        sa.Column("snapshot_sha256", sa.CHAR(64), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        created_at(),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(REVISION_STATE_CHECK, name=op.f("ck_revision_state")),
        sa.CheckConstraint(FROZEN_SNAPSHOT_CHECK, name=op.f("ck_revision_frozen_snapshot")),
        sa.ForeignKeyConstraint(["plan_id"], ["plan.id"], name=op.f("fk_revision_plan_id_plan")),
        sa.ForeignKeyConstraint(
            ["parent_revision_id"],
            ["revision.id"],
            name=op.f("fk_revision_parent_revision_id_revision"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["user.id"], name=op.f("fk_revision_created_by_user")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_revision")),
        sa.UniqueConstraint("plan_id", "number", name=op.f("uq_revision_plan_id_number")),
    )
    op.execute(CREATE_REJECT_FUNCTION)
    op.execute(CREATE_IMMUTABLE_TRIGGER)
    for column in ("current_revision_id", "validated_revision_id"):
        op.create_foreign_key(
            op.f(f"fk_plan_{column}_revision"), "plan", "revision", [column], ["id"]
        )

    op.create_table(
        "calibration",
        sa.Column("id", sa.Uuid(), server_default=UUID_DEFAULT, nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("image_to_paper", postgresql.JSONB(), nullable=True),
        sa.Column(
            "control_points",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("reprojection_error_mm", postgresql.JSONB(), nullable=True),
        sa.Column("paper_scale", sa.Integer(), nullable=True),
        sa.Column("local_frame", postgresql.JSONB(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("accepted_by", sa.Uuid(), nullable=False),
        sa.Column(
            "accepted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(CALIBRATION_METHOD_CHECK, name=op.f("ck_calibration_method")),
        sa.CheckConstraint(PAPER_SCALE_CHECK, name=op.f("ck_calibration_paper_scale")),
        sa.ForeignKeyConstraint(["plan_id"], ["plan.id"], name=op.f("fk_calibration_plan_id_plan")),
        sa.ForeignKeyConstraint(
            ["accepted_by"], ["user.id"], name=op.f("fk_calibration_accepted_by_user")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_calibration")),
    )
    op.create_index(op.f("ix_calibration_plan_id"), "calibration", ["plan_id"])
    op.create_index(
        "ux_calibration_plan_id_active",
        "calibration",
        ["plan_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )

    op.create_table(
        "entity",
        sa.Column("id", sa.Uuid(), server_default=UUID_DEFAULT, nullable=False),
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("layer", sa.Text(), nullable=False),
        sa.Column("geometry", postgresql.JSONB(), nullable=False),
        sa.Column("image_region", postgresql.JSONB(), nullable=True),
        sa.Column("attributes", postgresql.JSONB(), server_default=EMPTY_JSONB, nullable=False),
        sa.Column("evidence_id", sa.Uuid(), nullable=True),
        sa.Column("constraint", postgresql.JSONB(), nullable=True),
        sa.Column("source", postgresql.JSONB(), nullable=False),
        sa.Column("confidence", sa.Double(), nullable=True),
        sa.Column("model_version", sa.Text(), nullable=True),
        sa.Column("review_status", sa.Text(), nullable=False),
        sa.Column("metric_origin", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(ENTITY_TYPE_CHECK, name=op.f("ck_entity_type")),
        sa.CheckConstraint(REVIEW_STATUS_CHECK, name=op.f("ck_entity_review_status")),
        sa.CheckConstraint(METRIC_ORIGIN_CHECK, name=op.f("ck_entity_metric_origin")),
        sa.CheckConstraint(CONFIDENCE_CHECK, name=op.f("ck_entity_confidence")),
        sa.ForeignKeyConstraint(
            ["revision_id"], ["revision.id"], name=op.f("fk_entity_revision_id_revision")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["user.id"], name=op.f("fk_entity_created_by_user")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_entity")),
    )
    op.create_index(op.f("ix_entity_revision_id"), "entity", ["revision_id"])

    op.create_table(
        "catalog",
        sa.Column("id", sa.Uuid(), server_default=UUID_DEFAULT, nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("layers", postgresql.JSONB(), nullable=False),
        sa.Column("symbols", postgresql.JSONB(), nullable=False),
        created_at(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_catalog")),
        sa.UniqueConstraint("version", name=op.f("uq_catalog_version")),
    )
    op.create_index(
        "ux_catalog_active",
        "catalog",
        ["is_active"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )

    op.create_table(
        "artifact",
        sa.Column("id", sa.Uuid(), server_default=UUID_DEFAULT, nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("revision_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("is_draft", sa.Boolean(), nullable=False),
        sa.Column("sha256", sa.CHAR(64), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("generator_version", sa.Text(), nullable=False),
        created_at(),
        sa.CheckConstraint(ARTIFACT_KIND_CHECK, name=op.f("ck_artifact_kind")),
        sa.ForeignKeyConstraint(["plan_id"], ["plan.id"], name=op.f("fk_artifact_plan_id_plan")),
        sa.ForeignKeyConstraint(
            ["revision_id"], ["revision.id"], name=op.f("fk_artifact_revision_id_revision")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_artifact")),
    )
    op.create_index(
        "ix_artifact_plan_id_revision_id_kind", "artifact", ["plan_id", "revision_id", "kind"]
    )


def downgrade() -> None:
    op.drop_index("ix_artifact_plan_id_revision_id_kind", table_name="artifact")
    op.drop_table("artifact")
    op.drop_index("ux_catalog_active", table_name="catalog")
    op.drop_table("catalog")
    op.drop_index(op.f("ix_entity_revision_id"), table_name="entity")
    op.drop_table("entity")
    op.drop_index("ux_calibration_plan_id_active", table_name="calibration")
    op.drop_index(op.f("ix_calibration_plan_id"), table_name="calibration")
    op.drop_table("calibration")
    for column in ("validated_revision_id", "current_revision_id"):
        op.drop_constraint(op.f(f"fk_plan_{column}_revision"), "plan", type_="foreignkey")
    op.execute("DROP TRIGGER revision_frozen_immutable ON revision")
    op.execute("DROP FUNCTION reject_frozen_revision_mutation()")
    op.drop_table("revision")
    op.drop_index(op.f("ix_plan_primary_source_id"), table_name="plan")
    op.drop_index(op.f("ix_plan_case_file_id"), table_name="plan")
    op.drop_table("plan")

    bind = op.get_bind()
    metric_status.drop(bind)
    source_mode.drop(bind)
    plan_status.drop(bind)
