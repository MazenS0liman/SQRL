#!/usr/bin/python
"""
Notification Service
====================

Outbox-style notification inbox plus email delivery.

``emit`` only writes a row; it never talks to SMTP. A Celery beat task drains
pending rows via :meth:`send_pending_emails`, so a slow or unavailable mail
server can never block an API request or lose a notification.
"""
# ——————————————————————————————————————————————————————————————
# Imports

# Standard Libraries
import uuid
from typing import Any, Dict, List, Optional

# Logging
from loguru import logger

# Constants
from squirrel.constants.jobs import NotificationType

# Settings
from squirrel.core.config import settings

# Services
from squirrel.services.notifications.EmailService import EmailService
from squirrel.services.notifications.schema import NOTIFICATION_DDL_STATEMENTS
from squirrel.services.storage.database.PostgresService import PostgresService


class NotificationNotFoundError(Exception):
    """Raised when a notification id does not exist for the requesting user."""


class NotificationService:
    """Store notifications and deliver their email side-effects."""

    _NOTIFICATIONS_TABLE = "notifications"
    _PREFERENCES_TABLE = "notification_preferences"

    def __init__(
        self,
        db: Optional[PostgresService] = None,
        email_service: Optional[EmailService] = None,
    ) -> None:
        self._db = db
        self._email = email_service
        self._tables_ready = False

    # ------------------------------------------------------------------
    # Bootstrap

    def _get_db(self) -> PostgresService:
        if self._db is None:
            self._db = PostgresService()
        if not self._tables_ready:
            self._ensure_tables(self._db)
            self._tables_ready = True
        return self._db

    def _ensure_tables(self, db: PostgresService) -> None:
        for statement in NOTIFICATION_DDL_STATEMENTS:
            db.execute(statement, params={}, fetch=False)

    def _get_email_service(self) -> EmailService:
        if self._email is None:
            self._email = EmailService()
        return self._email

    # ------------------------------------------------------------------
    # Inbox

    def emit(
        self,
        user_id: str,
        title: str,
        *,
        notification_type: str = NotificationType.SYSTEM,
        body: str = "",
        link: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Record an in-app notification and queue its email side-effect.

        :param user_id: Recipient user id.
        :param title: Short headline shown in the inbox.
        :param notification_type: One of :class:`NotificationType`.
        :param body: Longer description.
        :param link: Relative in-app path (for example ``/jobs/<id>``).
        :return: The inserted notification row.
        """
        if notification_type not in NotificationType.ALL:
            raise ValueError(f"Unknown notification type: {notification_type!r}")
        if not user_id:
            raise ValueError("A user_id is required to emit a notification.")

        notification_id = str(uuid.uuid4())
        result = self._get_db().query(
            """
            INSERT INTO notifications (id, user_id, type, title, body, link)
            VALUES (%(id)s, %(user_id)s, %(type)s, %(title)s, %(body)s, %(link)s)
            RETURNING *
            """,
            params={
                "id": notification_id,
                "user_id": user_id,
                "type": notification_type,
                "title": title,
                "body": body,
                "link": link,
            },
            fetch=True,
        )
        row = (result or {}).get("rows", [{}])[0]
        logger.info(
            "Emitted notification {} (user={}, type={})",
            notification_id, user_id, notification_type,
        )
        return row

    def list_for_user(
        self,
        user_id: str,
        *,
        unread_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """List a user's notifications, newest first."""
        limit = max(1, min(200, int(limit)))
        offset = max(0, int(offset))
        unread_clause = "AND read_at IS NULL" if unread_only else ""
        result = self._get_db().query(
            f"""
            SELECT * FROM notifications
            WHERE user_id = %(user_id)s {unread_clause}
            ORDER BY created_at DESC
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            params={"user_id": user_id, "limit": limit, "offset": offset},
            fetch=True,
        )
        return (result or {}).get("rows") or []

    def unread_count(self, user_id: str) -> int:
        """Return the number of unread notifications for a user."""
        result = self._get_db().query(
            """
            SELECT COUNT(*) AS count
            FROM notifications
            WHERE user_id = %(user_id)s AND read_at IS NULL
            """,
            params={"user_id": user_id},
            fetch=True,
        )
        rows = (result or {}).get("rows") or []
        return int(rows[0]["count"]) if rows else 0

    def mark_read(self, notification_id: str, user_id: str) -> Dict[str, Any]:
        """Mark one notification as read, scoped to its owner."""
        result = self._get_db().query(
            """
            UPDATE notifications
            SET read_at = COALESCE(read_at, NOW())
            WHERE id = %(id)s AND user_id = %(user_id)s
            RETURNING *
            """,
            params={"id": notification_id, "user_id": user_id},
            fetch=True,
        )
        rows = (result or {}).get("rows") or []
        if not rows:
            raise NotificationNotFoundError(notification_id)
        return rows[0]

    def mark_all_read(self, user_id: str) -> int:
        """Mark every unread notification for a user as read."""
        result = self._get_db().query(
            """
            UPDATE notifications
            SET read_at = NOW()
            WHERE user_id = %(user_id)s AND read_at IS NULL
            """,
            params={"user_id": user_id},
            fetch=False,
        )
        return int((result or {}).get("rowcount") or 0)

    # ------------------------------------------------------------------
    # Preferences

    def get_preferences(self, user_id: str) -> Dict[str, Any]:
        """Return a user's notification preferences, creating defaults."""
        db = self._get_db()
        db.query(
            """
            INSERT INTO notification_preferences (user_id)
            VALUES (%(user_id)s)
            ON CONFLICT (user_id) DO NOTHING
            """,
            params={"user_id": user_id},
            fetch=False,
        )
        result = db.query(
            """
            SELECT * FROM notification_preferences
            WHERE user_id = %(user_id)s
            LIMIT 1
            """,
            params={"user_id": user_id},
            fetch=True,
        )
        rows = (result or {}).get("rows") or []
        return rows[0] if rows else {
            "user_id": user_id,
            "email_enabled": True,
            "digest_mode": "immediate",
        }

    def update_preferences(
        self,
        user_id: str,
        *,
        email_enabled: Optional[bool] = None,
        digest_mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update a user's notification preferences."""
        if digest_mode is not None and digest_mode not in {"immediate", "daily", "off"}:
            raise ValueError("digest_mode must be 'immediate', 'daily', or 'off'.")

        # Ensure the row exists before updating it.
        self.get_preferences(user_id)
        result = self._get_db().query(
            """
            UPDATE notification_preferences
            SET email_enabled = COALESCE(%(email_enabled)s, email_enabled),
                digest_mode   = COALESCE(%(digest_mode)s, digest_mode),
                updated_at    = NOW()
            WHERE user_id = %(user_id)s
            RETURNING *
            """,
            params={
                "user_id": user_id,
                "email_enabled": email_enabled,
                "digest_mode": digest_mode,
            },
            fetch=True,
        )
        rows = (result or {}).get("rows") or []
        return rows[0] if rows else self.get_preferences(user_id)

    # ------------------------------------------------------------------
    # Email outbox

    def pending_emails(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Return notifications that still need an email, joined with the
        recipient address and their delivery preference.
        """
        limit = max(1, min(200, int(limit)))
        result = self._get_db().query(
            """
            SELECT n.*, u.email AS recipient_email
            FROM notifications n
            JOIN auth_users u ON u.id = n.user_id
            LEFT JOIN notification_preferences p ON p.user_id = n.user_id
            WHERE n.email_sent_at IS NULL
              AND n.email_error IS NULL
              AND COALESCE(p.email_enabled, TRUE) = TRUE
            ORDER BY n.created_at ASC
            LIMIT %(limit)s
            """,
            params={"limit": limit},
            fetch=True,
        )
        return (result or {}).get("rows") or []

    def mark_email_sent(self, notification_id: str) -> None:
        """Record successful email delivery."""
        self._get_db().query(
            """
            UPDATE notifications
            SET email_sent_at = NOW(), email_error = NULL
            WHERE id = %(id)s
            """,
            params={"id": notification_id},
            fetch=False,
        )

    def mark_email_error(self, notification_id: str, error: str) -> None:
        """Record a permanent delivery failure so it is not retried forever."""
        self._get_db().query(
            """
            UPDATE notifications
            SET email_error = %(error)s
            WHERE id = %(id)s
            """,
            params={"id": notification_id, "error": error[:2000]},
            fetch=False,
        )

    def send_pending_emails(self, limit: int = 50) -> Dict[str, int]:
        """
        Deliver pending notification emails.

        Safe to run repeatedly: each row is claimed by its ``email_sent_at``
        timestamp, and failures are recorded instead of retried indefinitely.
        """
        if not settings.NOTIFICATIONS_ENABLED:
            return {"sent": 0, "failed": 0, "skipped": 0}

        email_service = self._get_email_service()
        if not email_service.is_configured:
            logger.warning(
                "NOTIFICATIONS_ENABLED is true but SMTP is not configured; "
                "skipping notification email delivery."
            )
            return {"sent": 0, "failed": 0, "skipped": len(self.pending_emails(limit))}

        sent = 0
        failed = 0
        for notification in self.pending_emails(limit):
            recipient = notification.get("recipient_email")
            if not recipient:
                self.mark_email_error(
                    notification["id"], "Recipient has no email address."
                )
                failed += 1
                continue

            link = notification.get("link")
            absolute_link = (
                f"{settings.APP_BASE_URL.rstrip('/')}{link}" if link else None
            )
            body = notification.get("body") or ""
            if absolute_link:
                body = f"{body}\n\n{absolute_link}".strip()

            try:
                email_service.send(
                    to=recipient,
                    subject=f"[{settings.APP_NAME}] {notification['title']}",
                    body=body or notification["title"],
                )
                self.mark_email_sent(notification["id"])
                sent += 1
            except Exception as exc:  # noqa: BLE001 - one bad address must not stop the batch
                logger.exception(
                    "Failed to email notification {}", notification.get("id")
                )
                self.mark_email_error(notification["id"], str(exc))
                failed += 1

        return {"sent": sent, "failed": failed, "skipped": 0}
