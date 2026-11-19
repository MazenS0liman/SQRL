"""phase 2: LLM usage ledger and model cards

Revision ID: 0004_usage_model_details
Revises: 0003_pipeline_outputs
Create Date: 2026-11-18

Adds the append-only ``llm_usage_events`` ledger used by the per-user usage
monitor and a ``details`` column on ``etl_models`` for model cards (expected
input schema, required preprocessing, and training history).

Everything is idempotent so it is safe on deployments whose tables were
created by the service bootstrap rather than Alembic.
"""

from alembic import op

from squirrel.services.pipelines.schema import ETL_MODELS_ALTER_DDL
from squirrel.services.usage.schema import LLM_USAGE_EVENTS_DDL


# revision identifiers, used by Alembic.
revision = "0004_usage_model_details"
down_revision = "0003_pipeline_outputs"
branch_labels = None
depends_on = None


def _execute_ddl(statements: tuple[str, ...]) -> None:
    for ddl in statements:
        for statement in ddl.split(";"):
            if statement.strip():
                op.execute(statement)


def upgrade() -> None:
    _execute_ddl((LLM_USAGE_EVENTS_DDL, ETL_MODELS_ALTER_DDL))


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS llm_usage_events CASCADE")
    op.execute("ALTER TABLE etl_models DROP COLUMN IF EXISTS details")
