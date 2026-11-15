"""Shared pytest fixtures for the SQRL backend.

The tests exercise service and task logic without a live PostgreSQL instance.
``FakeDB`` records every statement and replays queued results, which keeps the
tests fast while still asserting on the SQL and parameters a real
``PostgresService`` would receive.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


# Make ``import squirrel`` work regardless of the directory pytest is invoked
# from (CI runs from ``backend``, editors often run from the repo root).
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from tests.fakes import FakeDB  # noqa: E402  (import after sys.path setup)


@pytest.fixture
def fake_db() -> FakeDB:
    """A fresh in-memory database double."""
    return FakeDB()
