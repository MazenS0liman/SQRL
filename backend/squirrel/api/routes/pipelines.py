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

import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool

from squirrel.api.routes.auth import get_current_user
from squirrel.core.config import settings
from squirrel.modules.agents.etl.ETLPlannerAgent import ETLPlannerAgent
from squirrel.schemas.auth import AuthUserRead
from squirrel.schemas.etl import (
    ApprovePipelineRequest,
    BuildDashboardRequest,
    CreatePipelineRequest,
    MaterializePipelineRequest,
    MLModelRead,
    PipelineDashboardRead,
    PipelinePlanRead,
    PipelineRead,
    PipelineRunRead,
    PipelineStagePlanRead,
    PlanPipelineRequest,
    PlanStageRequest,
    ResolveSourceRead,
    ResolveSourceRequest,
    RunPipelineRequest,
    SourceSpec,
    SourceTableRead,
    UpdateDashboardLayoutRequest,
    UpdatePipelineRequest,
    UpdateScheduleRequest,
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
from squirrel.services.pipelines.PlannerContextBuilder import PlannerContextBuilder

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

_TABLE_TOKEN_RE = re.compile(r"\b([a-z][a-z0-9_]{0,62})\b")


def _mentioned_source_tables(
    svc: PipelineService,
    owner_user_id: str,
    query: str,
    explicit: List[str],
    exclude: Optional[str] = None,
) -> List[str]:
    """
    Best-effort list of *existing* app tables named in a chat message.

    Explicitly declared tables are always returned so the planner can report
    why they cannot be read. Auto-detected names are only kept when they resolve
    to a real table, which stops destination names (often ``etl_*`` too) from
    being reported as broken sources.
    """
    names: List[str] = []
    seen = set()
    prefixes = tuple(settings.etl_source_table_prefixes)
    allowed = set(settings.etl_allowed_source_tables)

    for name in explicit:
        if name not in seen and name != exclude:
            seen.add(name)
            names.append(name)

    for token in _TABLE_TOKEN_RE.findall((query or "").lower()):
        if token in seen or token == exclude:
            continue
        if not (token in allowed or token.startswith(prefixes)):
            continue
        try:
            svc.resolve_source_columns(
                SourceSpec(kind="table", table_name=token), owner_user_id
            )
        except Exception:  # noqa: BLE001 - likely a destination or a typo
            continue
        seen.add(token)
        names.append(token)
        if len(names) >= 20:
            break
    return names


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
    it, then create the pipeline with ``POST /pipelines``. Sending the latest
    proposal back as ``previous_definition`` turns the request into a
    refinement, which is what the chat builder does.
    """
    _ensure_enabled()

    def _plan() -> PipelinePlanRead:
        svc = _svc()
        table_names = _mentioned_source_tables(
            svc,
            current_user.id,
            payload.query,
            payload.table_names,
            exclude=payload.sink_table,
        )
        context = PlannerContextBuilder(svc).build(
            current_user.id,
            connector_id=payload.connector_id,
            table=payload.table,
            file_urls=payload.file_urls,
            table_names=table_names,
            extra=payload.context,
        )
        if "ml" in (payload.output_targets or []):
            try:
                context["available_models"] = [
                    {
                        "id": row.get("id"),
                        "name": row.get("name"),
                        "pipeline_id": row.get("pipeline_id"),
                        "task": row.get("task"),
                        "target": row.get("target"),
                    }
                    for row in svc.list_models(current_user.id)
                ][:50]
            except Exception:  # noqa: BLE001 - planning must still work
                context["available_models"] = []
        result = ETLPlannerAgent().plan(
            payload.query,
            connector_id=payload.connector_id,
            table=payload.table,
            file_urls=payload.file_urls,
            sink_table=payload.sink_table,
            notebook_id=payload.notebook_id,
            context=context,
            source_resolver=lambda spec: svc.resolve_source_columns(spec, current_user.id),
            previous_definition=payload.previous_definition,
            history=[turn.model_dump() for turn in payload.history],
            output_targets=payload.output_targets,
        )

        definition = result["definition"]
        sink_step = next(step for step in definition.steps if step.type == "sink")
        sink = sink_step.sink
        destination = None
        if sink is not None:
            destination = sink.table if sink.kind == "postgres" else sink.object_key

        step_count = len(definition.steps)
        reply = result["description"] or f"I've drafted '{result['name']}'."
        extras = []
        if definition.outputs and definition.outputs.dashboard:
            extras.append("a dashboard")
        if definition.outputs and definition.outputs.ml:
            ml = definition.outputs.ml
            mode_label = {
                "train": "train",
                "retrain": "retrain",
                "inference": "score rows with",
            }.get(ml.mode, "train")
            extras.append(
                f"a {ml.task} model to {mode_label}"
                + (f" `{ml.target}`" if ml.target else "")
            )
        outputs_note = f" After the run it also produces {' and '.join(extras)}." if extras else ""
        reply = (
            f"{reply} The plan has {step_count} step"
            f"{'' if step_count == 1 else 's'} and writes to "
            f"`{destination}`.{outputs_note} Refine it here, then create and approve it before "
            "the first production run."
        )
        return PipelinePlanRead(
            name=result["name"],
            description=result["description"],
            definition=definition,
            validation=result["validation"],
            reply=reply,
        )

    try:
        return await run_in_threadpool(_plan)
    except PipelineValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )


# ——————————————————————————————————————————————————————————————
# Source catalog and stage planning


@router.get(
    "/sources/tables",
    summary="List app tables that can be used as ETL sources",
    response_model=List[SourceTableRead],
)
async def list_source_tables(
    current_user: AuthUserRead = Depends(get_current_user),
) -> List[SourceTableRead]:
    """Tables the compiler will accept as ``source.kind = "table"``."""
    _ensure_enabled()
    rows = await run_in_threadpool(_svc().list_source_tables)
    return [SourceTableRead(**row) for row in rows]


@router.post(
    "/sources/resolve",
    summary="Resolve the columns exposed by one source",
    response_model=ResolveSourceRead,
)
async def resolve_source(
    payload: ResolveSourceRequest,
    current_user: AuthUserRead = Depends(get_current_user),
) -> ResolveSourceRead:
    """Used by the source editor to offer real columns for selection."""
    _ensure_enabled()
    try:
        columns = await run_in_threadpool(
            _svc().resolve_source_columns, payload.source, current_user.id
        )
    except PipelineValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:  # noqa: BLE001 - connector/file infrastructure
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not read this source: {exc}",
        ) from exc
    return ResolveSourceRead(columns=columns)


@router.post(
    "/stages/plan",
    summary="Insert a natural-language stage between two DAG steps",
    response_model=PipelineStagePlanRead,
)
async def plan_stage(
    payload: PlanStageRequest,
    current_user: AuthUserRead = Depends(get_current_user),
) -> PipelineStagePlanRead:
    """
    Convert a stage description into validated transform step(s).

    The planner receives the current definition, the insertion edge, and the
    real columns of every step. It returns a complete definition only after the
    compiler accepts it; nothing is persisted or executed.
    """
    _ensure_enabled()

    def _plan() -> PipelineStagePlanRead:
        svc = _svc()
        result = ETLPlannerAgent().plan_stage(
            payload.description,
            definition=payload.definition,
            insert_after=payload.insert_after,
            insert_before=payload.insert_before,
            source_resolver=lambda spec: svc.resolve_source_columns(spec, current_user.id),
        )
        return PipelineStagePlanRead(
            definition=result["definition"],
            steps=result["steps"],
            validation=result["validation"],
            reply=result["reply"],
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
    "/models",
    summary="List registered ETL models (retrain / inference candidates)",
    response_model=List[MLModelRead],
)
async def list_models(
    pipeline_id: Optional[str] = Query(default=None),
    current_user: AuthUserRead = Depends(get_current_user),
) -> List[MLModelRead]:
    rows = await run_in_threadpool(
        _svc().list_models, current_user.id, pipeline_id=pipeline_id
    )
    return [PipelineService.to_model_read(row) for row in rows]


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
    summary="Build (or rebuild) the pipeline dashboard",
    response_model=PipelineDashboardRead,
)
async def materialize_pipeline(
    pipeline_id: str,
    payload: Optional[MaterializePipelineRequest] = None,
    current_user: AuthUserRead = Depends(get_current_user),
) -> PipelineDashboardRead:
    _ensure_enabled()
    payload = payload or MaterializePipelineRequest()
    try:
        await run_in_threadpool(
            _svc().materialize_pipeline,
            pipeline_id,
            payload.notebook_id,
            current_user.id,
            regenerate=payload.regenerate,
        )
        return await run_in_threadpool(
            _svc().get_dashboard, pipeline_id, current_user.id
        )
    except PipelineNotFoundError:
        raise _not_found("Pipeline", pipeline_id)
    except PipelineValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


# ——————————————————————————————————————————————————————————————
# Dashboard


@router.get(
    "/{pipeline_id}/dashboard",
    summary="Get the pipeline's latest dashboard",
    response_model=PipelineDashboardRead,
)
async def get_pipeline_dashboard(
    pipeline_id: str,
    current_user: AuthUserRead = Depends(get_current_user),
) -> PipelineDashboardRead:
    try:
        return await run_in_threadpool(
            _svc().get_dashboard, pipeline_id, current_user.id
        )
    except PipelineNotFoundError:
        raise _not_found("Pipeline", pipeline_id)
    except PipelineValidationError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.post(
    "/{pipeline_id}/dashboard",
    summary="Build (or rebuild) the pipeline's dashboard",
    response_model=PipelineDashboardRead,
)
async def build_pipeline_dashboard(
    pipeline_id: str,
    payload: Optional[BuildDashboardRequest] = None,
    current_user: AuthUserRead = Depends(get_current_user),
) -> PipelineDashboardRead:
    _ensure_enabled()
    regenerate = bool(payload.regenerate) if payload else False
    try:
        await run_in_threadpool(
            _svc().materialize_pipeline,
            pipeline_id,
            None,
            current_user.id,
            regenerate=regenerate,
        )
        return await run_in_threadpool(
            _svc().get_dashboard, pipeline_id, current_user.id
        )
    except PipelineNotFoundError:
        raise _not_found("Pipeline", pipeline_id)
    except PipelineValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.put(
    "/{pipeline_id}/dashboard/layout",
    summary="Save dashboard customisations",
    response_model=PipelineDashboardRead,
)
async def save_pipeline_dashboard_layout(
    pipeline_id: str,
    payload: UpdateDashboardLayoutRequest,
    current_user: AuthUserRead = Depends(get_current_user),
) -> PipelineDashboardRead:
    _ensure_enabled()
    try:
        return await run_in_threadpool(
            _svc().save_dashboard_layout,
            pipeline_id,
            payload.layout,
            current_user.id,
        )
    except PipelineNotFoundError:
        raise _not_found("Pipeline", pipeline_id)
    except PipelineValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


# ——————————————————————————————————————————————————————————————
# Scheduling


@router.put(
    "/{pipeline_id}/schedule",
    summary="Set a pipeline's automatic schedule",
    response_model=PipelineRead,
)
async def update_pipeline_schedule(
    pipeline_id: str,
    payload: UpdateScheduleRequest,
    current_user: AuthUserRead = Depends(get_current_user),
) -> PipelineRead:
    _ensure_enabled()
    try:
        row = await run_in_threadpool(
            _svc().update_schedule,
            pipeline_id,
            current_user.id,
            payload.schedule,
        )
    except PipelineNotFoundError:
        raise _not_found("Pipeline", pipeline_id)
    except PipelineValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return PipelineService.to_pipeline_read(row)
