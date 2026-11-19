#!/usr/bin/python
"""
Notification Schemas
====================

API models for the in-app notification inbox and delivery preferences.
"""
# ——————————————————————————————————————————————————————————————
# Imports

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class NotificationRead(BaseModel):
    """One notification in a user's inbox."""

    id: str
    user_id: str
    type: str
    title: str
    body: str = ""
    link: Optional[str] = None
    read_at: Optional[datetime] = None
    email_sent_at: Optional[datetime] = None
    email_error: Optional[str] = None
    created_at: Optional[datetime] = None


class NotificationListResponse(BaseModel):
    """A page of notifications plus the user's total unread count."""

    items: List[NotificationRead] = Field(default_factory=list)
    unread_count: int = 0


class NotificationPreferencesRead(BaseModel):
    """Per-user notification delivery preferences."""

    user_id: str
    email_enabled: bool = True
    digest_mode: str = "immediate"
    updated_at: Optional[datetime] = None


class NotificationPreferencesUpdate(BaseModel):
    """Fields a user may change on their notification preferences."""

    email_enabled: Optional[bool] = None
    digest_mode: Optional[Literal["immediate", "daily", "off"]] = None


class UnreadCountResponse(BaseModel):
    """Unread notification count for the current user."""

    count: int = 0


class MarkAllReadResponse(BaseModel):
    """Number of notifications marked read."""

    updated: int = 0
