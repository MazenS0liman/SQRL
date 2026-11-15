#!/usr/bin/python
"""
Notification Schema
===================

DDL for the in-app notification inbox and per-user delivery preferences.
"""
# ——————————————————————————————————————————————————————————————
# Imports
from __future__ import annotations


NOTIFICATIONS_DDL = """
CREATE TABLE IF NOT EXISTS notifications (
    id              TEXT             PRIMARY KEY,
    user_id         TEXT             NOT NULL,
    type            TEXT             NOT NULL,
    title           TEXT             NOT NULL,
    body            TEXT             NOT NULL DEFAULT '',
    link            TEXT,
    read_at         TIMESTAMPTZ,
    email_sent_at   TIMESTAMPTZ,
    email_error     TEXT,
    created_at      TIMESTAMPTZ      NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_notifications_user_created
    ON notifications (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notifications_pending_email
    ON notifications (email_sent_at)
    WHERE email_sent_at IS NULL;
"""


NOTIFICATION_PREFERENCES_DDL = """
CREATE TABLE IF NOT EXISTS notification_preferences (
    user_id         TEXT             PRIMARY KEY,
    email_enabled   BOOLEAN          NOT NULL DEFAULT TRUE,
    digest_mode     TEXT             NOT NULL DEFAULT 'immediate',
    updated_at      TIMESTAMPTZ      NOT NULL DEFAULT NOW()
);
"""


NOTIFICATION_DDL_STATEMENTS = (NOTIFICATIONS_DDL, NOTIFICATION_PREFERENCES_DDL)
