"""Alembic environment for SQRL.

Reads the database URL from the application settings so migrations and the
running app always target the same database.
"""
# ——————————————————————————————————————————————————————————————
# Imports

from logging.config import fileConfig
from pathlib import Path
import sys

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make ``squirrel`` importable when alembic runs from the backend directory.
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from squirrel.core.config import settings  # noqa: E402


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# SQLAlchemy needs the psycopg3 dialect prefix. Escape ``%`` because Alembic
# interpolates the value into an ini-style string.
database_url = settings.database_url
if database_url.startswith("postgresql://"):
    database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))

# No SQLAlchemy models are used by the app (services issue raw SQL), so there
# is no autogenerate metadata. Revisions are written explicitly.
target_metadata = None


def run_migrations_offline() -> None:
    """Run migrations without a live database connection."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
