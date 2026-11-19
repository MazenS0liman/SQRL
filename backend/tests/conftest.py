"""Shared pytest fixtures for the SQRL backend.

The tests exercise service and task logic without a live PostgreSQL instance.
``FakeDB`` records every statement and replays queued results, which keeps the
tests fast while still asserting on the SQL and parameters a real
``PostgresService`` would receive.

Secrets
-------
CI has no ``.env`` file, so provider API keys and the token-encryption key would
otherwise be empty. Provider clients (for example ``genai.Client``) and
``TokenService`` raise on empty credentials, which broke tests that never talk
to a real provider. Dummy values are injected here, before ``squirrel`` is
imported, so settings that are read at import time pick them up.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet


# Make ``import squirrel`` work regardless of the directory pytest is invoked
# from (CI runs from ``backend``, editors often run from the repo root).
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


# Dummy credentials. ``setdefault`` keeps any value already provided by the
# environment, but none of the tests make real provider calls.
_TEST_ENV = {
    "GEMINI_API_KEY_V1": "test-gemini-key",
    "GROQ_API_KEY_V1": "test-groq-key",
    "OPENROUTER_API_KEY_V1": "test-openrouter-key",
    "TOKEN_ENCRYPTION_KEY": Fernet.generate_key().decode(),
}
for _name, _value in _TEST_ENV.items():
    os.environ.setdefault(_name, _value)

from tests.fakes import FakeDB  # noqa: E402  (import after sys.path/env setup)


@pytest.fixture(autouse=True)
def _test_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guarantee non-empty secrets even if settings were loaded earlier.

    If ``squirrel.core.config.settings`` was already instantiated (for example
    by an import in another conftest or plugin), the environment variables
    above would be too late, so the attributes are patched directly as well.
    """
    from squirrel.core.config import settings

    for name, value in _TEST_ENV.items():
        if hasattr(settings, name) and not getattr(settings, name):
            monkeypatch.setattr(settings, name, value, raising=False)


@pytest.fixture
def fake_db() -> FakeDB:
    """A fresh in-memory database double."""
    return FakeDB()