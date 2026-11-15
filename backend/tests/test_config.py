"""Tests for the Phase 0 settings additions."""
from __future__ import annotations

from squirrel.core.config import Settings


def test_celery_urls_fall_back_to_redis() -> None:
    settings = Settings(
        REDIS_URL="redis://example:6379/2",
        CELERY_BROKER_URL="",
        CELERY_RESULT_BACKEND="",
    )

    assert settings.celery_broker_url == "redis://example:6379/2"
    assert settings.celery_result_backend == "redis://example:6379/2"


def test_celery_urls_prefer_explicit_overrides() -> None:
    settings = Settings(
        REDIS_URL="redis://redis:6379/0",
        CELERY_BROKER_URL="amqp://broker:5672//",
        CELERY_RESULT_BACKEND="redis://results:6379/1",
    )

    assert settings.celery_broker_url == "amqp://broker:5672//"
    assert settings.celery_result_backend == "redis://results:6379/1"


def test_notification_defaults_are_off() -> None:
    settings = Settings(NOTIFICATIONS_ENABLED=False, SMTP_HOST="")

    assert settings.NOTIFICATIONS_ENABLED is False
    assert settings.SMTP_HOST == ""
    assert settings.SMTP_PORT == 587
    assert settings.APP_BASE_URL == "http://localhost:5173"


def test_database_url_prefers_database_url() -> None:
    settings = Settings(
        DATABASE_URL="postgresql://primary/db",
        POSTGRES_DSN="postgresql://legacy/db",
    )

    assert settings.database_url == "postgresql://primary/db"


def test_database_url_falls_back_to_postgres_dsn() -> None:
    settings = Settings(DATABASE_URL="", POSTGRES_DSN="postgresql://legacy/db")

    assert settings.database_url == "postgresql://legacy/db"


def test_cors_origins_are_trimmed() -> None:
    settings = Settings(CORS_ORIGINS="http://a.test, http://b.test ,")

    assert settings.cors_origins == ["http://a.test", "http://b.test"]
