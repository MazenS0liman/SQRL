"""phase 0: durable jobs and notification outbox

Revision ID: 0001_phase0_foundation
Revises:
Create Date: 2026-11-13

Creates the shared foundation for asynchronous features:
``jobs``, ``job_events``, ``notifications``, and
``notification_preferences``.

The services still bootstrap these tables idempotently, so existing
deployments that do not run Alembic keep working; this revision is the
canonical migration for new environments and future schema changes.
"""

from alembic import op

from squirrel.services.jobs.schema import JOB_DDL_STATEMENTS
from squirrel.services.notifications.schema import NOTIFICATION_DDL_STATEMENTS


# revision identifiers, used by Alembic.
revision = "0001_phase0_foundation"
down_revision = None
branch_labels = None
depends_on = None


def _execute_ddl(statements: tuple[str, ...]) -> None:
    for ddl in statements:
        for statement in ddl.split(";"):
            if statement.strip():
                op.execute(statement)


def upgrade() -> None:
    _execute_ddl(JOB_DDL_STATEMENTS)
    _execute_ddl(NOTIFICATION_DDL_STATEMENTS)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS notification_preferences")
    op.execute("DROP TABLE IF EXISTS notifications")
    op.execute("DROP TABLE IF EXISTS job_events")
    op.execute("DROP TABLE IF EXISTS jobs")
