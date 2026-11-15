#!/usr/bin/python
"""
Job Routes
==========

Read-only API for durable background jobs and their event logs.

Jobs are created internally (and enqueued through Celery) by feature modules;
this router only exposes their state to the owning user.
"""
# ——————————————————————————————————————————————————————————————
# Imports

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool

from squirrel.api.routes.auth import get_current_user
from squirrel.constants.jobs import JobStatus
from squirrel.schemas.auth import AuthUserRead
from squirrel.schemas.jobs import JobEventRead, JobRead
from squirrel.services.jobs.JobService import JobNotFoundError, JobService


router = APIRouter()


def _svc() -> JobService:
    return JobService()


@router.get(
    "",
    summary="List background jobs for the current user",
    response_model=List[JobRead],
)
async def list_jobs(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    job_type: Optional[str] = Query(default=None, alias="type"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: AuthUserRead = Depends(get_current_user),
) -> List[JobRead]:
    if status_filter is not None and status_filter not in JobStatus.ALL:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown job status: {status_filter!r}",
        )
    try:
        rows = await run_in_threadpool(
            _svc().list_jobs,
            current_user.id,
            status=status_filter,
            job_type=job_type,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return [JobRead(**row) for row in rows]


@router.get(
    "/{job_id}",
    summary="Get one background job",
    response_model=JobRead,
)
async def get_job(
    job_id: str,
    current_user: AuthUserRead = Depends(get_current_user),
) -> JobRead:
    try:
        row = await run_in_threadpool(_svc().get_job, job_id, current_user.id)
    except JobNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_id}' not found.",
        )
    return JobRead(**row)


@router.get(
    "/{job_id}/events",
    summary="List a job's event log",
    response_model=List[JobEventRead],
)
async def list_job_events(
    job_id: str,
    limit: int = Query(default=200, ge=1, le=1000),
    current_user: AuthUserRead = Depends(get_current_user),
) -> List[JobEventRead]:
    try:
        rows = await run_in_threadpool(
            _svc().list_events, job_id, current_user.id, limit=limit
        )
    except JobNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_id}' not found.",
        )
    return [JobEventRead(**row) for row in rows]
