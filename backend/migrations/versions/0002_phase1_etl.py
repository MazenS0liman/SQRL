"""phase 1: ETL pipelines and run history

Revision ID: 0002_phase1_etl
Revises: 0001_phase0_foundation
Create Date: 2026-11-14

Creates the Phase 1 ETL tables: ``pipelines`` and ``pipeline_runs``.

``pipeline_runs.job_id`` references the Phase 0 ``jobs`` table, so this
revision depends on ``0001_phase0_foundation``. As with Phase 0, the service
still bootstraps these tables idempotently for deployments that do not run
Alembic; this is the canonical migration for new environments.
"""

from alembic import op

from squirrel.services.pipelines.schema import PIPELINE_DDL_STATEMENTS


# revision identifiers, used by Alembic.
revision = "0002_phase1_etl"
down_revision = "0001_phase0_foundation"
branch_labels = None
depends_on = None


def _execute_ddl(statements: tuple[str, ...]) -> None:
    for ddl in statements:
        for statement in ddl.split(";"):
            if statement.strip():
                op.execute(statement)


def upgrade() -> None:
    _execute_ddl(PIPELINE_DDL_STATEMENTS)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS pipeline_runs CASCADE")
    op.execute("DROP TABLE IF EXISTS pipelines CASCADE")
