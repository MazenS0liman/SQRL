"""phase 1: pipeline schedules, owned dashboards, and model registry

Revision ID: 0003_pipeline_outputs
Revises: 0002_phase1_etl
Create Date: 2026-11-17

Adds the automatic schedule columns and dashboard pointers to ``pipelines``,
plus the ``etl_models`` registry used by the retrain / inference ML modes.

Everything is idempotent so it is safe on deployments whose ``pipelines``
table was created by the service bootstrap rather than Alembic.
"""

from alembic import op

from squirrel.services.pipelines.schema import (
    ETL_MODELS_DDL,
    PIPELINE_ALTER_DDL,
)


# revision identifiers, used by Alembic.
revision = "0003_pipeline_outputs"
down_revision = "0002_phase1_etl"
branch_labels = None
depends_on = None


def _execute_ddl(statements: tuple[str, ...]) -> None:
    for ddl in statements:
        for statement in ddl.split(";"):
            if statement.strip():
                op.execute(statement)


def upgrade() -> None:
    _execute_ddl((PIPELINE_ALTER_DDL, ETL_MODELS_DDL))


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS etl_models CASCADE")
    op.execute("DROP INDEX IF EXISTS idx_pipelines_due")
    for column in (
        "schedule_mode",
        "schedule_enabled",
        "schedule_time",
        "schedule_day",
        "schedule_month",
        "schedule_interval_seconds",
        "next_run_at",
        "last_scheduled_at",
        "dashboard_notebook_id",
        "dashboard_cell_id",
    ):
        op.execute(f"ALTER TABLE pipelines DROP COLUMN IF EXISTS {column}")
