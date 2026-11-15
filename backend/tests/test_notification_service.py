"""Tests for :class:`squirrel.services.notifications.NotificationService`."""
from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

from squirrel.constants.jobs import NotificationType
from squirrel.services.notifications.EmailService import EmailService
from squirrel.services.notifications.NotificationService import (
    NotificationNotFoundError,
    NotificationService,
)
from tests.fakes import FakeDB


notification_module = importlib.import_module(
    "squirrel.services.notifications.NotificationService"
)


class RecordingEmailService:
    """Email double that records successful sends."""

    is_configured = True

    def __init__(self) -> None:
        self.sent: list[dict] = []

    def send(self, to: str, subject: str, body: str, html: str | None = None) -> None:
        self.sent.append({"to": to, "subject": subject, "body": body})


class FlakyEmailService(RecordingEmailService):
    """Email double that rejects one specific recipient."""

    def send(self, to: str, subject: str, body: str, html: str | None = None) -> None:
        if to == "bad@example.test":
            raise RuntimeError("mailbox unavailable")
        super().send(to, subject, body, html)


def _patch_settings(monkeypatch: pytest.MonkeyPatch, **overrides) -> None:
    values = {
        "NOTIFICATIONS_ENABLED": True,
        "APP_BASE_URL": "https://app.example/",
        "APP_NAME": "SQRL",
    }
    values.update(overrides)
    monkeypatch.setattr(notification_module, "settings", SimpleNamespace(**values))


# ---------------------------------------------------------------------------
# Inbox
# ---------------------------------------------------------------------------

def test_emit_writes_row_without_sending_email(fake_db: FakeDB) -> None:
    fake_db.queue({"rows": [{"id": "n-1"}]})
    service = NotificationService(db=fake_db, email_service=RecordingEmailService())

    row = service.emit(
        "user-1",
        "Pipeline finished",
        notification_type=NotificationType.JOB_SUCCEEDED,
        body="10 rows written.",
        link="/jobs/job-1",
    )

    assert row == {"id": "n-1"}
    query = fake_db.last_query
    assert "INSERT INTO notifications" in query.sql
    assert query.params["user_id"] == "user-1"
    assert query.params["type"] == NotificationType.JOB_SUCCEEDED
    assert query.params["title"] == "Pipeline finished"
    assert query.params["body"] == "10 rows written."
    assert query.params["link"] == "/jobs/job-1"
    assert query.fetch is True


def test_emit_validates_input(fake_db: FakeDB) -> None:
    service = NotificationService(db=fake_db)

    with pytest.raises(ValueError):
        service.emit("user-1", "Title", notification_type="not-a-type")
    with pytest.raises(ValueError):
        service.emit("", "Title")

    assert fake_db.queries == []


def test_list_for_user_unread_only_clamps_pagination(fake_db: FakeDB) -> None:
    fake_db.queue({"rows": [{"id": "n-1"}]})
    service = NotificationService(db=fake_db)

    rows = service.list_for_user("user-1", unread_only=True, limit=999, offset=-1)

    assert rows == [{"id": "n-1"}]
    query = fake_db.last_query
    assert "AND read_at IS NULL" in query.sql
    assert query.params["limit"] == 200
    assert query.params["offset"] == 0


def test_list_for_user_all_has_no_unread_filter(fake_db: FakeDB) -> None:
    fake_db.queue({"rows": []})
    service = NotificationService(db=fake_db)

    service.list_for_user("user-1")

    assert "read_at IS NULL" not in fake_db.last_query.sql


def test_unread_count_returns_integer(fake_db: FakeDB) -> None:
    fake_db.queue({"rows": [{"count": "3"}]})

    assert NotificationService(db=fake_db).unread_count("user-1") == 3


def test_unread_count_defaults_to_zero(fake_db: FakeDB) -> None:
    fake_db.queue({"rows": []})

    assert NotificationService(db=fake_db).unread_count("user-1") == 0


def test_mark_read_scopes_to_owner(fake_db: FakeDB) -> None:
    fake_db.queue({"rows": [{"id": "n-1", "user_id": "user-1"}]})
    service = NotificationService(db=fake_db)

    row = service.mark_read("n-1", "user-1")

    assert row["id"] == "n-1"
    query = fake_db.last_query
    assert "user_id = %(user_id)s" in query.sql
    assert query.params == {"id": "n-1", "user_id": "user-1"}


def test_mark_read_missing_raises(fake_db: FakeDB) -> None:
    fake_db.queue({"rows": []})

    with pytest.raises(NotificationNotFoundError):
        NotificationService(db=fake_db).mark_read("n-1", "user-1")


def test_mark_all_read_returns_rowcount(fake_db: FakeDB) -> None:
    fake_db.queue({"rowcount": 4})

    updated = NotificationService(db=fake_db).mark_all_read("user-1")

    assert updated == 4
    assert fake_db.last_query.fetch is False


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------

def test_get_preferences_returns_defaults_when_missing(fake_db: FakeDB) -> None:
    fake_db.queue({"rowcount": 0}, {"rows": []})
    service = NotificationService(db=fake_db)

    prefs = service.get_preferences("user-1")

    assert prefs == {
        "user_id": "user-1",
        "email_enabled": True,
        "digest_mode": "immediate",
    }
    assert "ON CONFLICT (user_id) DO NOTHING" in fake_db.queries[0].sql


def test_update_preferences_rejects_unknown_digest_mode(fake_db: FakeDB) -> None:
    service = NotificationService(db=fake_db)

    with pytest.raises(ValueError):
        service.update_preferences("user-1", digest_mode="weekly")

    assert fake_db.queries == []


def test_update_preferences_updates_existing_row(fake_db: FakeDB) -> None:
    fake_db.queue(
        {"rowcount": 0},
        {"rows": [{"user_id": "user-1", "email_enabled": True, "digest_mode": "immediate"}]},
        {"rows": [{"user_id": "user-1", "email_enabled": False, "digest_mode": "daily"}]},
    )
    service = NotificationService(db=fake_db)

    prefs = service.update_preferences(
        "user-1", email_enabled=False, digest_mode="daily"
    )

    assert prefs["email_enabled"] is False
    assert prefs["digest_mode"] == "daily"
    query = fake_db.last_query
    assert "UPDATE notification_preferences" in query.sql
    assert query.params["email_enabled"] is False
    assert query.params["digest_mode"] == "daily"


# ---------------------------------------------------------------------------
# Email outbox
# ---------------------------------------------------------------------------

def test_pending_emails_joins_recipient_and_preferences(fake_db: FakeDB) -> None:
    fake_db.queue({"rows": [{"id": "n-1", "recipient_email": "a@b.test"}]})
    service = NotificationService(db=fake_db)

    rows = service.pending_emails(limit=999)

    assert rows[0]["recipient_email"] == "a@b.test"
    query = fake_db.last_query
    assert "JOIN auth_users" in query.sql
    assert "LEFT JOIN notification_preferences" in query.sql
    assert "email_sent_at IS NULL" in query.sql
    assert query.params["limit"] == 200


def test_send_pending_emails_is_noop_when_disabled(
    fake_db: FakeDB,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_settings(monkeypatch, NOTIFICATIONS_ENABLED=False)
    service = NotificationService(db=fake_db)

    result = service.send_pending_emails()

    assert result == {"sent": 0, "failed": 0, "skipped": 0}
    assert fake_db.queries == []


def test_send_pending_emails_skips_when_smtp_unconfigured(
    fake_db: FakeDB,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_settings(monkeypatch)
    fake_db.queue({"rows": [{"id": "n-1"}, {"id": "n-2"}]})
    service = NotificationService(
        db=fake_db,
        email_service=EmailService(host="", sender=""),
    )

    result = service.send_pending_emails()

    assert result == {"sent": 0, "failed": 0, "skipped": 2}


def test_send_pending_emails_delivers_and_marks_sent(
    fake_db: FakeDB,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_settings(monkeypatch)
    fake_db.queue(
        {
            "rows": [
                {
                    "id": "n-1",
                    "recipient_email": "a@b.test",
                    "title": "Pipeline finished",
                    "body": "All good.",
                    "link": "/jobs/job-1",
                }
            ]
        },
        {"rowcount": 1},
    )
    email = RecordingEmailService()
    service = NotificationService(db=fake_db, email_service=email)

    result = service.send_pending_emails()

    assert result == {"sent": 1, "failed": 0, "skipped": 0}
    assert email.sent[0]["to"] == "a@b.test"
    assert email.sent[0]["subject"] == "[SQRL] Pipeline finished"
    assert "All good." in email.sent[0]["body"]
    assert "https://app.example/jobs/job-1" in email.sent[0]["body"]
    assert "email_sent_at = NOW()" in fake_db.last_query.sql


def test_send_pending_emails_marks_missing_recipient_as_failed(
    fake_db: FakeDB,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_settings(monkeypatch)
    fake_db.queue(
        {"rows": [{"id": "n-1", "recipient_email": None, "title": "Title"}]},
        {"rowcount": 1},
    )
    service = NotificationService(
        db=fake_db,
        email_service=RecordingEmailService(),
    )

    result = service.send_pending_emails()

    assert result == {"sent": 0, "failed": 1, "skipped": 0}
    query = fake_db.last_query
    assert "email_error" in query.sql
    assert query.params["error"] == "Recipient has no email address."


def test_send_pending_emails_continues_after_delivery_error(
    fake_db: FakeDB,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_settings(monkeypatch)
    fake_db.queue(
        {
            "rows": [
                {
                    "id": "n-1",
                    "recipient_email": "bad@example.test",
                    "title": "One",
                    "body": "",
                    "link": None,
                },
                {
                    "id": "n-2",
                    "recipient_email": "ok@example.test",
                    "title": "Two",
                    "body": "",
                    "link": None,
                },
            ]
        },
        {"rowcount": 1},
        {"rowcount": 1},
    )
    email = FlakyEmailService()
    service = NotificationService(db=fake_db, email_service=email)

    result = service.send_pending_emails()

    assert result == {"sent": 1, "failed": 1, "skipped": 0}
    assert [item["to"] for item in email.sent] == ["ok@example.test"]
    assert "email_error" in fake_db.queries[1].sql
    assert fake_db.queries[1].params["id"] == "n-1"
    assert "email_sent_at = NOW()" in fake_db.queries[2].sql
    assert fake_db.queries[2].params["id"] == "n-2"
