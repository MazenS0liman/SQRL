#!/usr/bin/python
"""
Pipeline Routes
===============

API for the Phase 1 ETL feature:

- CRUD for constrained pipeline definitions.
- Human approval before the first production run.
- Queued runs with durable job/event history.
- Dry runs that validate and compile without writing a destination.
- Materialisation of a Postgres sink as a notebook dashboard.
- Natural-language planning through :class:`ETLPlannerAgent`.

A pipeline definition is never executed as arbitrary code. Every request is
validated by :class:`~squirrel.services.pipelines.compiler.PipelineCompiler`
before it is stored or run.
"""
# ——————————————————————————————————————————————————————————————
# Imports

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool

from squirrel.api.routes.auth import get_current_user
from squirrel.core.config import settings
from squirrel.modules.agents.etl.ETLPlannerAgent import ETLPlannerAgent
from squirrel.schemas.auth import AuthUserRead
from squirrel.schemas.etl import (
    ApprovePipelineRequest,
    CreatePipelineRequest,
    MaterializePipelineRequest,
    PipelinePlanRead,
    PipelineRead,
    PipelineRunRead,
    PlanPipelineRequest,
    RunPipelineRequest,
    UpdatePipelineRequest,
)
from squirrel.schemas.jobs import JobEventRead
from squirrel.services.jobs.JobService import JobNotFoundError, JobService
from squirrel.services.pipelines.PipelineService import (
    PipelineNotApprovedError,
    PipelineNotFoundError,
    PipelineRunNotFoundError,
    PipelineService,
)
from squirrel.services.pipelines.compiler import PipelineValidationError


router = APIRouter()


def _svc() -> PipelineService:
    return PipelineService()


def _jobs() -> JobService:
    return JobService()


def _ensure_enabled() -> None:
    if not settings.ETL_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The ETL feature is disabled on this deployment.",
        )


def _not_found(entity: str, identifier: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"{entity} '{identifier}' not found.",
    )


# ——————————————————————————————————————————————————————————————
# Planning


@router.post(
    "/plan",
    summary="Plan a pipeline from natural language",
    response_model=PipelinePlanRead,
)
async def plan_pipeline(
    payload: PlanPipelineRequest,
    current_user: AuthUserRead = Depends(get_current_user),
) -> PipelinePlanRead:
    """
    Ask the ETL planner for a validated proposal.

    The plan is not persisted and nothing is executed. The caller can review
    it, then create the pipeline with ``POST /pipelines``.
    """
    _ensure_enabled()

    def _plan() -> PipelinePlanRead:
        result = ETLPlannerAgent().plan(
            payload.query,
            connector_id=payload.connector_id,
            table=payload.table,
            file_urls=payload.file_urls,
            sink_table=payload.sink_table,
            notebook_id=payload.notebook_id,
            context=payload.context,
        )
        return PipelinePlanRead(
            name=result["name"],
            description=result["description"],
            definition=result["definition"],
            validation=result["validation"],
        )

    try:
        return await run_in_threadpool(_plan)
    except PipelineValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )


# ——————————————————————————————————————————————————————————————
# Pipeline CRUD


@router.post(
    "",
    summary="Create a pipeline awaiting approval",
    response_model=PipelineRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_pipeline(
    payload: CreatePipelineRequest,
    current_user: AuthUserRead = Depends(get_current_user),
) -> PipelineRead:
    _ensure_enabled()
    try:
        row = await run_in_threadpool(
            _svc().create_pipeline,
            current_user.id,
            payload.name,
            payload.definition,
            payload.description,
        )
    except PipelineValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return PipelineService.to_pipeline_read(row)


@router.get(
    "",
    summary="List pipelines for the current user",
    response_model=List[PipelineRead],
)
async def list_pipelines(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: AuthUserRead = Depends(get_current_user),
) -> List[PipelineRead]:
    rows = await run_in_threadpool(
        _svc().list_pipelines,
        current_user.id,
        limit=limit,
        offset=offset,
    )
    return [PipelineService.to_pipeline_read(row) for row in rows]


@router.get(
    "/runs/{run_id}",
    summary="Get one pipeline run",
    response_model=PipelineRunRead,
)
async def get_run(
    run_id: str,
    current_user: AuthUserRead = Depends(get_current_user),
) -> PipelineRunRead:
    try:
        row = await run_in_threadpool(_svc().get_run, run_id, current_user.id)
    except PipelineRunNotFoundError:
        raise _not_found("Pipeline run", run_id)
    return PipelineService.to_run_read(row)


@router.get(
    "/runs/{run_id}/events",
    summary="List a pipeline run's job events",
    response_model=List[JobEventRead],
)
async def list_run_events(
    run_id: str,
    limit: int = Query(default=200, ge=1, le=1000),
    current_user: AuthUserRead = Depends(get_current_user),
) -> List[JobEventRead]:
    try:
        run = await run_in_threadpool(_svc().get_run, run_id, current_user.id)
    except PipelineRunNotFoundError:
        raise _not_found("Pipeline run", run_id)

    job_id = run.get("job_id")
    if not job_id:
        return []
    try:
        rows = await run_in_threadpool(
            _jobs().list_events, job_id, current_user.id, limit=limit
        )
    except JobNotFoundError:
        return []
    return [JobEventRead(**row) for row in rows]


@router.get(
    "/{pipeline_id}",
    summary="Get one pipeline",
    response_model=PipelineRead,
)
async def get_pipeline(
    pipeline_id: str,
    current_user: AuthUserRead = Depends(get_current_user),
) -> PipelineRead:
    try:
        row = await run_in_threadpool(_svc().get_pipeline, pipeline_id, current_user.id)
    except PipelineNotFoundError:
        raise _not_found("Pipeline", pipeline_id)
    return PipelineService.to_pipeline_read(row)


@router.patch(
    "/{pipeline_id}",
    summary="Update a pipeline (resets approval)",
    response_model=PipelineRead,
)
async def update_pipeline(
    pipeline_id: str,
    payload: UpdatePipelineRequest,
    current_user: AuthUserRead = Depends(get_current_user),
) -> PipelineRead:
    _ensure_enabled()
    try:
        row = await run_in_threadpool(
            _svc().update_pipeline,
            pipeline_id,
            current_user.id,
            name=payload.name,
            description=payload.description,
            definition=payload.definition,
        )
    except PipelineNotFoundError:
        raise _not_found("Pipeline", pipeline_id)
    except PipelineValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return PipelineService.to_pipeline_read(row)


@router.delete(
    "/{pipeline_id}",
    summary="Delete a pipeline and its run history",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_pipeline(
    pipeline_id: str,
    current_user: AuthUserRead = Depends(get_current_user),
) -> None:
    _ensure_enabled()
    try:
        await run_in_threadpool(_svc().delete_pipeline, pipeline_id, current_user.id)
    except PipelineNotFoundError:
        raise _not_found("Pipeline", pipeline_id)


# ——————————————————————————————————————————————————————————————
# Approval and runs


@router.post(
    "/{pipeline_id}/approve",
    summary="Approve the current pipeline definition",
    response_model=PipelineRead,
)
async def approve_pipeline(
    pipeline_id: str,
    payload: Optional[ApprovePipelineRequest] = None,
    current_user: AuthUserRead = Depends(get_current_user),
) -> PipelineRead:
    _ensure_enabled()
    try:
        row = await run_in_threadpool(
            _svc().approve_pipeline,
            pipeline_id,
            current_user.id,
            current_user.id,
        )
    except PipelineNotFoundError:
        raise _not_found("Pipeline", pipeline_id)
    return PipelineService.to_pipeline_read(row)


@router.post(
    "/{pipeline_id}/runs",
    summary="Queue a pipeline run (dry_run=true to validate only)",
    response_model=PipelineRunRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_run(
    pipeline_id: str,
    payload: Optional[RunPipelineRequest] = None,
    current_user: AuthUserRead = Depends(get_current_user),
) -> PipelineRunRead:
    _ensure_enabled()
    dry_run = bool(payload.dry_run) if payload else False
    try:
        row = await run_in_threadpool(
            _svc().create_run,
            pipeline_id,
            current_user.id,
            dry_run=dry_run,
            enqueue=True,
        )
    except PipelineNotFoundError:
        raise _not_found("Pipeline", pipeline_id)
    except PipelineNotApprovedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except PipelineValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return PipelineService.to_run_read(row)


@router.get(
    "/{pipeline_id}/runs",
    summary="List a pipeline's run history",
    response_model=List[PipelineRunRead],
)
async def list_runs(
    pipeline_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: AuthUserRead = Depends(get_current_user),
) -> List[PipelineRunRead]:
    try:
        rows = await run_in_threadpool(
            _svc().list_runs,
            pipeline_id,
            current_user.id,
            limit=limit,
            offset=offset,
        )
    except PipelineNotFoundError:
        raise _not_found("Pipeline", pipeline_id)
    return [PipelineService.to_run_read(row) for row in rows]


@router.post(
    "/{pipeline_id}/materialize",
    summary="Expose the latest Postgres sink as a notebook dashboard",
)
async def materialize_pipeline(
    pipeline_id: str,
    payload: MaterializePipelineRequest,
    current_user: AuthUserRead = Depends(get_current_user),
) -> Dict[str, Any]:
    _ensure_enabled()
    try:
        result = await run_in_threadpool(
            _svc().materialize_pipeline,
            pipeline_id,
            payload.notebook_id,
            current_user.id,
        )
    except PipelineNotFoundError:
        raise _not_found("Pipeline", pipeline_id)
    except PipelineValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return result
