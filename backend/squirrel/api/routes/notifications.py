#!/usr/bin/python
"""
Notification Routes
===================

In-app notification inbox and per-user delivery preferences.
"""
# ——————————————————————————————————————————————————————————————
# Imports

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool

from squirrel.api.routes.auth import get_current_user
from squirrel.schemas.auth import AuthUserRead
from squirrel.schemas.notifications import (
    MarkAllReadResponse,
    NotificationListResponse,
    NotificationPreferencesRead,
    NotificationPreferencesUpdate,
    NotificationRead,
    UnreadCountResponse,
)
from squirrel.services.notifications.NotificationService import (
    NotificationNotFoundError,
    NotificationService,
)


router = APIRouter()


def _svc() -> NotificationService:
    return NotificationService()


@router.get(
    "",
    summary="List the current user's notifications",
    response_model=NotificationListResponse,
)
async def list_notifications(
    unread_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: AuthUserRead = Depends(get_current_user),
) -> NotificationListResponse:
    svc = _svc()
    rows = await run_in_threadpool(
        svc.list_for_user,
        current_user.id,
        unread_only=unread_only,
        limit=limit,
        offset=offset,
    )
    unread = await run_in_threadpool(svc.unread_count, current_user.id)
    return NotificationListResponse(
        items=[NotificationRead(**row) for row in rows],
        unread_count=unread,
    )


@router.get(
    "/unread-count",
    summary="Count the current user's unread notifications",
    response_model=UnreadCountResponse,
)
async def unread_count(
    current_user: AuthUserRead = Depends(get_current_user),
) -> UnreadCountResponse:
    count = await run_in_threadpool(_svc().unread_count, current_user.id)
    return UnreadCountResponse(count=count)


@router.post(
    "/read-all",
    summary="Mark all of the current user's notifications as read",
    response_model=MarkAllReadResponse,
)
async def mark_all_read(
    current_user: AuthUserRead = Depends(get_current_user),
) -> MarkAllReadResponse:
    updated = await run_in_threadpool(_svc().mark_all_read, current_user.id)
    return MarkAllReadResponse(updated=updated)


@router.get(
    "/preferences",
    summary="Get the current user's notification preferences",
    response_model=NotificationPreferencesRead,
)
async def get_preferences(
    current_user: AuthUserRead = Depends(get_current_user),
) -> NotificationPreferencesRead:
    row = await run_in_threadpool(_svc().get_preferences, current_user.id)
    return NotificationPreferencesRead(**row)


@router.patch(
    "/preferences",
    summary="Update the current user's notification preferences",
    response_model=NotificationPreferencesRead,
)
async def update_preferences(
    body: NotificationPreferencesUpdate,
    current_user: AuthUserRead = Depends(get_current_user),
) -> NotificationPreferencesRead:
    try:
        row = await run_in_threadpool(
            _svc().update_preferences,
            current_user.id,
            email_enabled=body.email_enabled,
            digest_mode=body.digest_mode,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return NotificationPreferencesRead(**row)


@router.post(
    "/{notification_id}/read",
    summary="Mark one notification as read",
    response_model=NotificationRead,
)
async def mark_read(
    notification_id: str,
    current_user: AuthUserRead = Depends(get_current_user),
) -> NotificationRead:
    try:
        row = await run_in_threadpool(
            _svc().mark_read, notification_id, current_user.id
        )
    except NotificationNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Notification '{notification_id}' not found.",
        )
    return NotificationRead(**row)
