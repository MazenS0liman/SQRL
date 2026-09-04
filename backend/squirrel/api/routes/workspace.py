#!/usr/bin/python
"""
Workspace Route
===============

Overview
--------

Every workspace is identified by its ``workspace_id`` — that's the primary
key throughout, both in :class:`WorkspaceService` and in every URL below.

Pipeline per workspace
----------------------

1. ``POST /workspace``
   Create an empty workspace (name + data type). Returns its
   ``workspace_id``, which every later call for this workspace is keyed on.

2. ``POST /workspace/{workspace_id}/upload``
   Upload one or more files as input sources. Each file is stored in MinIO
   as its own input source. Only CSV uploads are actually parsed today
   (their column names come back so the frontend can populate the
   "predict this column" dropdown) — the upload path itself is generic,
   so adding a new file type (plain text, a zip of files, ...) is a matter
   of registering a parser (see ``_SOURCE_PARSERS`` below) and a loader on
   ``WorkspaceService``, not rewriting this endpoint.

3. ``POST /workspace/{workspace_id}/sources/connector``
   Attach an existing data connector (see the Data Connectors page) as an
   additional input source, instead of / alongside uploaded files.

4. ``DELETE /workspace/{workspace_id}/sources/{source_id}``
   Remove a single input source from a workspace.

5. ``POST /workspace/{workspace_id}/build``
   Given the ``target_column`` chosen in the dropdown (and, optionally, a
   free-text ``query`` describing how sources relate and/or what the model
   should optimize for):
     a. Loads every input source's data (downloading uploads from MinIO,
        querying connectors).
     b. Merges all sources into a single frame. When the sources don't
        share an identical schema or an obviously-disjoint one, this step
        is delegated to the preprocessing agent, which uses the LLM to
        infer a relationship between sources (e.g. a shared join key) —
        grounded in ``target_column`` and ``query`` — rather than the
        route guessing or refusing outright. If the agent can't find a
        defensible relationship, or two sources genuinely collide on a
        non-target column with no way to reconcile them, the build is
        refused (409) so the frontend can surface it.
     c. Runs :class:`TabularDataProcessorAgent` (clean + transform + feature
        engineer) on the merged frame, persisting that run — including the
        fitted plan + execution report needed to replay this exact
        preprocessing at inference time — via :class:`WorkspaceService`.
     d. Runs :class:`TabularDataModelBuilderAgent` on the processed frame
        and persists the fitted models + comparison summary.
     e. Returns both summaries, including per-model accuracy metrics, so
        the frontend can render a comparison table.

6. ``GET /workspace`` / ``GET /library/workspaces/{workspace_id}``
   List workspaces / fetch one workspace, for the Library page.

7. ``PATCH /workspace/{workspace_id}``
   Rename a workspace and/or change its declared data type.

8. ``DELETE /workspace/{workspace_id}``
   Permanently delete a workspace, its run history, and its stored
   artifacts. The frontend is expected to confirm this with the user
   before calling it.

9. ``GET /workspace/{workspace_id}/models``
   Convenience endpoint returning just the latest model-building summary
   (model list + accuracy metrics + recommended model) for the workspace.

10. ``POST /workspace/{workspace_id}/predict``
    Given new, raw rows shaped like the workspace's original input
    source(s), replays the workspace's saved fitted preprocessing pipeline
    on them (reusing training-time fitted parameters — means, bounds,
    encoding maps, etc. — rather than re-fitting from the new rows) and
    runs the chosen (or best) fitted model to produce predictions.

.. note::
    Data-type values other than ``"structured"`` (image / text / audio /
    other) are recorded on the workspace so the frontend can route to a
    dedicated pipeline later, but only ``"structured"`` currently drives
    the preprocessing/model-building pipeline below; attempting to
    ``/build`` a non-structured workspace returns 400.

"""
# ——————————————————————————————————————————————————————————————
# Imports

# Standard Libraries
import json
import io
import re
import tempfile
from pathlib import Path
from typing import Optional, List, Dict, Any

# Third-Party Libraries
import pandas as pd
from fastapi import APIRouter, Body, Depends, File, HTTPException, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

# Services
from squirrel.services.workspace.WorkspaceService import (
    WorkspaceService,
    WorkspaceStatus,
    DataType,
    SourceKind,
    DuplicateColumnError,
)

# Schema
from squirrel.services.data_connector.DataConnectorService import (
    ConnectorNotFoundError
)
from squirrel.schemas.connector import (
    ConnectorSourcesRequest
)
from squirrel.schemas.workspace import (
    SourceColumnsRequest
)
from squirrel.api.routes.auth import get_current_user
from squirrel.schemas.auth import AuthUserRead


# Agents
from squirrel.modules.agents import (
    TabularDataInspectorAgent,
    TabularDataProcessorAgent,
    TabularDataModelBuilderAgent
)

# Schema
from squirrel.schemas.workspace import (
    WorkspaceRecord,
    DataSource,
    WorkspaceCreateRequest,
    WorkspaceUpdateRequest,
    ConnectorSourceRequest,
    UploadResponse,
    BuildRequest,
    BuildResponse,
    ModelsResponse,
    PreprocessedDataResponse,
    ModelMetric,
    ModelFileOut,
    PredictRequest,     # NEW — {"rows": [...], "model_key": Optional[str]}
    PredictResponse,     # NEW — {"model_key", "predictions", "probabilities"?, "classes"?}
)
from squirrel.schemas.file import UploadedSourceOut

# Exception
from squirrel.schemas.error import WorkspaceNotFoundError, SourceNotFoundError

# Logging
from loguru import logger

# API Router
router = APIRouter()

# ——————————————————————————————————————————————————————————————
# Upload parsers — one entry per supported file extension.
#
# Extension point: to accept a new input-source file type, add a parser
# here that returns {"columns", "row_count", "preview"} (tabular) or
# {"metadata": {...}} (non-tabular), and a matching loader in
# ``WorkspaceService._upload_loaders``. Nothing else in this route needs
# to change — only ``csv`` is wired up today, everything else 501s.


def _parse_csv(tmp_path: str, file_name: str) -> Dict[str, Any]:
    try:
        df = pd.read_csv(tmp_path)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not parse '{file_name}': {exc}",
        )
    preview = df.head(10).where(pd.notnull(df.head(10)), None).to_dict(orient="records")
    return {"columns": list(df.columns), "row_count": int(df.shape[0]), "preview": preview}


_SOURCE_PARSERS = {
    "csv": _parse_csv,
    # "txt": _parse_text,     # not yet implemented — add a parser + loader to enable
    # "zip": _parse_archive,  # not yet implemented — add a parser + loader to enable
}

# Which upload extensions are acceptable for a given workspace data_type.
# ``None`` (data types not listed) means "no restriction" — useful for
# data types whose upload pipeline isn't defined yet.
_ALLOWED_EXTENSIONS_BY_DATA_TYPE: Dict[str, set] = {
    DataType.STRUCTURED: {".csv"},
}


# ——————————————————————————————————————————————————————————————
# Helpers

def _workspace_service() -> WorkspaceService:
    return WorkspaceService()


def _not_found(workspace_id: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Workspace '{workspace_id}' not found.",
    )


def _extract_model_comparison(model_summary: Dict[str, Any]) -> List[ModelMetric]:
    raw = model_summary.get("model_comparison") or []
    metrics: List[ModelMetric] = []
    for entry in raw:
        try:
            metrics.append(
                ModelMetric(
                    model_key=str(entry.get("model_key", "")),
                    metric_name=str(entry.get("metric_name", "")),
                    mean=float(entry.get("mean", 0.0)),
                    std=float(entry["std"]) if entry.get("std") is not None else None,
                )
            )
        except (TypeError, ValueError):
            logger.warning("Skipping malformed model_comparison entry: {}", entry)
    return metrics


def _model_key_from_url(file_url: str) -> str:
    """
    Recover the ``model_key`` from a stored model artifact URL.

    :class:`WorkspaceService._upload_model` names uploaded objects
    ``{workspace_id}/models/{model_key}_{timestamp}.joblib``. This reverses
    that naming convention so the API can expose a proper model_key ->
    file_url mapping without requiring a database schema change (the
    ``output_file_urls`` column stores plain strings).

    Falls back to the bare filename stem if the timestamp suffix isn't
    found, so this degrades gracefully for any unexpected naming.
    """
    filename = file_url.rsplit("/", 1)[-1]
    stem = filename[: -len(".joblib")] if filename.endswith(".joblib") else filename
    match = re.match(r"^(.*)_\d{8}T\d{6}$", stem)
    return match.group(1) if match else stem


# ——————————————————————————————————————————————————————————————
# Endpoints — workspace CRUD


@router.post(
    "",
    summary="Create a new workspace",
    status_code=status.HTTP_201_CREATED,
    response_model=WorkspaceRecord,
)
async def create_workspace(
    request: WorkspaceCreateRequest = Body(...),
    current_user: AuthUserRead = Depends(get_current_user),
) -> WorkspaceRecord:
    """
    Create an empty workspace that one or more data sources can later be
    added to.
    """
    if request.data_type not in DataType.ALL:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown data_type '{request.data_type}'.",
        )
    try:
        row = await run_in_threadpool(
            _workspace_service().create_workspace, request.name, request.data_type, current_user.id
        )
    except Exception:
        logger.exception("Failed to create workspace '{}'", request.name)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create workspace.",
        )
    return WorkspaceRecord(**row)


@router.get(
    "",
    summary="List all workspaces",
    response_model=List[WorkspaceRecord],
)
async def list_workspaces(
    current_user: AuthUserRead = Depends(get_current_user),
) -> List[WorkspaceRecord]:
    """
    List every workspace, newest first, for the Library page.
    """
    rows = await run_in_threadpool(_workspace_service().list_workspaces, 200, current_user.id)
    return [WorkspaceRecord(**row) for row in rows]


@router.get(
    "/{workspace_id}",
    summary="Get a single workspace",
    response_model=WorkspaceRecord,
)
async def get_workspace(
    workspace_id: str,
    current_user: AuthUserRead = Depends(get_current_user),
) -> WorkspaceRecord:
    try:
        row = await run_in_threadpool(_workspace_service().get_workspace, workspace_id, current_user.id)
    except WorkspaceNotFoundError:
        raise _not_found(workspace_id)
    return WorkspaceRecord(**row)


@router.patch(
    "/{workspace_id}",
    summary="Rename a workspace",
    response_model=WorkspaceRecord,
)
async def update_workspace(
    workspace_id: str,
    request: WorkspaceUpdateRequest = Body(...),
    current_user: AuthUserRead = Depends(get_current_user),
) -> WorkspaceRecord:
    """
    Edit a workspace's display name.
    """
    workspace_svc = _workspace_service()
    try:
        await run_in_threadpool(workspace_svc.get_workspace, workspace_id, current_user.id)
    except WorkspaceNotFoundError:
        raise _not_found(workspace_id)

    if request.name is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide a new 'name' to update.",
        )

    try:
        row = await run_in_threadpool(workspace_svc.rename_workspace, workspace_id, request.name, current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    return WorkspaceRecord(**row)


@router.delete(
    "/{workspace_id}",
    summary="Permanently delete a workspace",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_workspace(
    workspace_id: str,
    current_user: AuthUserRead = Depends(get_current_user),
) -> Response:
    """
    Permanently delete a workspace: its run history, stored artifacts, and
    the workspace row itself. This cannot be undone.
    """
    workspace_svc = _workspace_service()
    try:
        await run_in_threadpool(workspace_svc.delete_workspace, workspace_id, True, current_user.id)
    except WorkspaceNotFoundError:
        raise _not_found(workspace_id)
    except Exception:
        logger.exception("Failed to delete workspace workspace_id={}", workspace_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete workspace.",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ——————————————————————————————————————————————————————————————
# Endpoints — input sources


@router.post(
    "/{workspace_id}/upload",
    summary="Upload one or more files into a workspace as input sources",
    response_model=UploadResponse,
)
async def upload_files(
    workspace_id: str,
    files: List[UploadFile] = File(...),
    current_user: AuthUserRead = Depends(get_current_user),
) -> UploadResponse:
    """
    Upload one or more files into a workspace, each becoming its own input
    source. Stores every file in MinIO regardless of type; only file types
    with a registered parser (currently just CSV) are actually inspected
    for columns/row-count/preview — anything else 501s until a parser is
    added, rather than silently mis-handling it.
    """
    workspace_svc = _workspace_service()
    try:
        workspace = await run_in_threadpool(workspace_svc.get_workspace, workspace_id, current_user.id)
    except WorkspaceNotFoundError:
        raise _not_found(workspace_id)

    if not files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No files provided.")

    allowed_extensions = _ALLOWED_EXTENSIONS_BY_DATA_TYPE.get(workspace.get("data_type"))
    if allowed_extensions is not None:
        for file in files:
            suffix = Path(file.filename or "").suffix.lower()
            if suffix not in allowed_extensions:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"'{file.filename}' has an unsupported extension for a "
                        f"'{workspace.get('data_type')}' workspace. Allowed: "
                        f"{', '.join(sorted(allowed_extensions))}."
                    ),
                )

    uploaded_out: List[UploadedSourceOut] = []

    for file in files:
        suffix = Path(file.filename or "").suffix.lower().lstrip(".")
        parser = _SOURCE_PARSERS.get(suffix)
        if parser is None:
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail=(
                    f"Uploads of type '.{suffix}' aren't supported yet. "
                    f"Currently supported: {', '.join(sorted(_SOURCE_PARSERS)) or 'none'}."
                ),
            )

        contents = await file.read()
        with tempfile.NamedTemporaryFile(suffix=f".{suffix}", delete=False) as tmp:
            tmp.write(contents)
            tmp_path = tmp.name

        try:
            parsed = parser(tmp_path, file.filename)

            source = await run_in_threadpool(
                workspace_svc.add_file_source,
                workspace_id,
                tmp_path,
                file.filename,
                suffix,
                parsed.get("columns"),
                parsed.get("row_count"),
                current_user.id,
            )

            uploaded_out.append(
                UploadedSourceOut(
                    source_id=source["source_id"],
                    file_name=file.filename,
                    file_type=suffix,
                    columns=parsed.get("columns"),
                    row_count=parsed.get("row_count"),
                    preview=parsed.get("preview"),
                    metadata=parsed.get("metadata"),
                )
            )
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    refreshed = await run_in_threadpool(workspace_svc.get_workspace, workspace_id, current_user.id)
    return UploadResponse(
        workspace_id=workspace_id,
        uploaded=uploaded_out,
        sources=[DataSource(**s) for s in (refreshed.get("input_sources") or [])],
    )


async def add_connector_source(
    workspace_id: str,
    request: ConnectorSourceRequest = Body(...),
    current_user: AuthUserRead = Depends(get_current_user),
) -> List[DataSource]:
    """
    Attach a data connector as an input source, scoped to exactly the data
    described by ``request.query`` (built from a table picked via
    GET /connectors/{connector_id}/tables, or typed freehand and checked
    via POST /connectors/{connector_id}/preview) — never the connector's
    full dataset.
    """
    workspace_svc = _workspace_service()
    try:
        await run_in_threadpool(workspace_svc.get_workspace, workspace_id, current_user.id)
    except WorkspaceNotFoundError:
        raise _not_found(workspace_id)

    try:
        await run_in_threadpool(
            workspace_svc.add_connector_source,
            workspace_id, request.connector_id, request.name, request.query, None, current_user.id,
        )
    except ConnectorNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Connector '{request.connector_id}' not found.",
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    refreshed = await run_in_threadpool(workspace_svc.get_workspace, workspace_id, current_user.id)
    return [DataSource(**s) for s in (refreshed.get("input_sources") or [])]

@router.delete(
    "/{workspace_id}/sources/{source_id}",
    summary="Remove an input source from a workspace",
    response_model=List[DataSource],
)
async def remove_source(
    workspace_id: str, 
    source_id: str,
    current_user: AuthUserRead = Depends(get_current_user),
) -> List[DataSource]:
    """
    Remove a single input source (upload or connector) from a workspace.
    """
    workspace_svc = _workspace_service()
    try:
        await run_in_threadpool(workspace_svc.get_workspace, workspace_id, current_user.id)
    except WorkspaceNotFoundError:
        raise _not_found(workspace_id)

    try:
        await run_in_threadpool(workspace_svc.remove_source, workspace_id, source_id, current_user.id)
    except SourceNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source '{source_id}' not found on this workspace.",
        )

    refreshed = await run_in_threadpool(workspace_svc.get_workspace, workspace_id, current_user.id)
    return [DataSource(**s) for s in (refreshed.get("input_sources") or [])]


# ——————————————————————————————————————————————————————————————
# Endpoints — build


@router.post(
    "/{workspace_id}/build/structured",
    summary="Preprocess every input source and build models against a target column for structured data.",
    response_model=BuildResponse,
)
async def build_structured_models(                                                                                                                                                                             
    workspace_id: str, 
    request: BuildRequest = Body(...),
    current_user: AuthUserRead = Depends(get_current_user),
) -> BuildResponse:
    """
    Run the full pipeline for a workspace: load every input source, merge
    them into one training frame (deterministically when possible, via the
    preprocessing agent's LLM-grounded relationship-finding otherwise),
    preprocess it, then fit and compare models against ``target_column``.

    ``request.query`` — if given — is passed through to both the
    preprocessing and model-building agents as free-text context: it's
    most useful when the sources don't share an identical or obviously
    disjoint schema and the agent has to infer how they relate (e.g. "join
    the two sources on customer_id") or what the model should prioritize.

    Both stages are persisted through :class:`WorkspaceService`, so this
    workspace's run history is queryable the same way a chat-driven run
    would be. The preprocessing stage additionally persists the fitted
    plan + execution report as a standalone "pipeline artifact" — see
    :meth:`WorkspaceService.save_pipeline_artifact` — so that
    ``POST /{workspace_id}/predict`` can later replay the exact same
    preprocessing against new rows.
    """
    workspace_svc = _workspace_service()
    try:
        workspace = await run_in_threadpool(workspace_svc.get_workspace, workspace_id, current_user.id)
    except WorkspaceNotFoundError:
        raise _not_found(workspace_id)

    if workspace.get("data_type") != DataType.STRUCTURED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Workspace data_type is '{workspace.get('data_type')}'; the tabular "
                "model-building pipeline only supports 'structured' workspaces today."
            ),
        )

    sources = workspace.get("input_sources") or []
    if not sources:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Add at least one CSV upload or connected data source before building models.",
        )

    # ------------------------------------------------------------------ #
    # Step 1 — Load every source back into memory
    # ------------------------------------------------------------------ #
    try:
        dataframes = await run_in_threadpool(workspace_svc.load_source_dataframes, sources)
    except NotImplementedError as exc:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc))
    except Exception as exc:
        logger.exception("Failed to load source data for workspace={}", workspace_id)
        await run_in_threadpool(
            workspace_svc.set_status, workspace_id, WorkspaceStatus.FAILED, None, str(exc), current_user.id
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load one or more input sources.",
        )

    if not any(request.target_column in df.columns for df in dataframes.values()):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Column '{request.target_column}' was not found in any input source.",
        )

    input_file_urls: List[str] = [
        s["file_url"] for s in sources if s.get("kind") == SourceKind.UPLOAD and s.get("file_url")
    ]

    await run_in_threadpool(
        workspace_svc.set_status, workspace_id, WorkspaceStatus.PREPROCESSING, request.target_column, None, current_user.id
    )

    inspector = TabularDataInspectorAgent()
    inspection_report = json.loads(
        await run_in_threadpool(inspector.inspect_multi, dataframes, request.query or "")
    )
    await run_in_threadpool(
        workspace_svc.save_run, workspace_id, "inspection", input_file_urls, None, inspection_report, 0, 0, 0, 0, current_user.id,
    )
    relationship = inspection_report.get("relationship") or {}

    try:
        processor = TabularDataProcessorAgent()
        # run_multi() returns (processed_df, summary, plan, execution_report).
        # The last two capture every strategy's fitted, data-dependent
        # parameters (means, bounds, encoding maps, fitted power-transform
        # λ, one-hot dummy-column sets, ...) — they must be forwarded to
        # save_run() below so the workspace becomes predictable via
        # POST /{workspace_id}/predict. Previously these were discarded here.
        processed_df, preprocessing_summary, preprocessing_plan, preprocessing_execution_report = (
            await run_in_threadpool(
                processor.run_multi,
                dataframes,
                request.target_column,
                inspection_report,          # eda_summary — full multi-source findings
                request.query or "",
                relationship,               # merge_recommendation
                2,                          # max_refinements
            )
        )
    except DuplicateColumnError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": (
                    "These columns appear in more than one data source, so the "
                    "sources can't be merged unambiguously. Rename or remove the "
                    "duplicate column(s), or describe how the sources relate in "
                    "'query', then try again."
                ),
                "conflicting_columns": exc.columns,
            },
        )
    except ValueError as exc:
        # Raised when the agent couldn't find a defensible relationship
        # between sources (see TabularDataProcessorAgent._merge_via_agent).
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": str(exc)},
        )
    except Exception as exc:
        logger.exception("Preprocessing failed for workspace={}", workspace_id)
        await run_in_threadpool(
            workspace_svc.set_status, workspace_id, WorkspaceStatus.FAILED, None, str(exc), current_user.id
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Preprocessing failed.",
        )

    preprocess_save = await run_in_threadpool(
        workspace_svc.save_run,
        workspace_id,
        "preprocessing",
        input_file_urls,
        processed_df,
        preprocessing_summary,
        0,
        0,
        0,
        0,
        current_user.id,
        preprocessing_plan,               # NEW — persisted as part of the pipeline artifact
        preprocessing_execution_report,   # NEW — carries the fitted state predict() replays
    )
    output_file_urls: List[str] = list(preprocess_save.get("output_file_urls", []))

    # ------------------------------------------------------------------ #
    # Step 3 — Build & compare models
    # ------------------------------------------------------------------ #
    await run_in_threadpool(workspace_svc.set_status, workspace_id, WorkspaceStatus.MODELING, None, None, current_user.id)

    try:
        builder = TabularDataModelBuilderAgent(target_column=request.target_column)
        fitted_models, model_summary = await run_in_threadpool(
            builder.run, processed_df, preprocessing_summary, request.query or ""
        )
    except Exception as exc:
        logger.exception("Model building failed for workspace={}", workspace_id)
        await run_in_threadpool(
            workspace_svc.set_status, workspace_id, WorkspaceStatus.FAILED, request.target_column, str(exc), current_user.id
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Model building failed.",
        )

    model_save = await run_in_threadpool(
        workspace_svc.save_run,
        workspace_id,
        "model_building",
        output_file_urls,
        fitted_models,
        model_summary,
        0,
        0,
        0,
        0,
        current_user.id,
    )
    model_output_urls = list(model_save.get("output_file_urls", []))
    output_file_urls.extend(model_output_urls)

    model_files = [
        ModelFileOut(model_key=_model_key_from_url(url), file_url=url)
        for url in model_output_urls
    ]

    await run_in_threadpool(
        workspace_svc.set_status, workspace_id, WorkspaceStatus.COMPLETED, request.target_column, None, current_user.id
    )

    return BuildResponse(
        workspace_id=workspace_id,
        status=WorkspaceStatus.COMPLETED,
        preprocessing_summary=preprocessing_summary,
        model_summary=model_summary,
        model_comparison=_extract_model_comparison(model_summary),
        best_model=model_summary.get("best_model"),
        output_file_urls=output_file_urls,
        model_files=model_files
    )


# ——————————————————————————————————————————————————————————————
# Endpoints — models / results


@router.get(
    "/{workspace_id}/models",
    summary="Get the latest model comparison + accuracy for a workspace",
    response_model=ModelsResponse,
)
async def get_models(
    workspace_id: str,
    current_user: AuthUserRead = Depends(get_current_user),
) -> ModelsResponse:
    """Return the most recent model-building summary for a workspace, for the results panel."""
    workspace_svc = _workspace_service()
    try:
        workspace = await run_in_threadpool(workspace_svc.get_workspace, workspace_id, current_user.id)
    except WorkspaceNotFoundError:
        raise _not_found(workspace_id)

    records = await run_in_threadpool(
        workspace_svc.get_records_by_workspace_id,
        workspace_id,
        "model_building",
        1,
        current_user.id,
    )

    if not records:
        return ModelsResponse(
            workspace_id=workspace_id,
            status=workspace["status"],
            target_column=workspace.get("target_column"),
        )

    latest = records[0]
    model_summary = latest.get("agent_summary") or {}
    model_output_urls = list(latest.get("output_file_urls", []) or [])
    preprocessing_records = await run_in_threadpool(
        workspace_svc.get_records_by_workspace_id,
        workspace_id,
        "preprocessing",
        1,
        current_user.id,
    )
    preprocessing_summary = (
        (preprocessing_records[0].get("agent_summary") or {})
        if preprocessing_records
        else None
    )

    return ModelsResponse(
        workspace_id=workspace_id,
        status=workspace["status"],
        target_column=workspace.get("target_column"),
        preprocessing_summary=preprocessing_summary,
        model_summary=model_summary,
        model_comparison=_extract_model_comparison(model_summary),
        best_model=model_summary.get("best_model"),
        output_file_urls=model_output_urls,
        model_files=[
            ModelFileOut(model_key=_model_key_from_url(url), file_url=url)
            for url in model_output_urls
        ]
    )


@router.get(
    "/{workspace_id}/preprocessed",
    summary="Preview the latest data after the training preprocessing pipeline",
    response_model=PreprocessedDataResponse,
)
async def get_preprocessed_data(
    workspace_id: str,
    limit: int = 100,
    current_user: AuthUserRead = Depends(get_current_user),
) -> PreprocessedDataResponse:
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="limit must be between 1 and 500.")

    workspace_svc = _workspace_service()
    try:
        workspace = await run_in_threadpool(workspace_svc.get_workspace, workspace_id, current_user.id)
    except WorkspaceNotFoundError:
        raise _not_found(workspace_id)

    records = await run_in_threadpool(
        workspace_svc.get_records_by_workspace_id,
        workspace_id,
        "preprocessing",
        1,
        current_user.id,
    )
    if not records:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No preprocessing run found for this workspace.")

    urls = records[0].get("output_file_urls") or []
    csv_url = next((url for url in urls if str(url).lower().endswith(".csv")), None)
    if not csv_url:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No preprocessed data artifact found.")

    raw = await run_in_threadpool(workspace_svc.download_output_file, csv_url)
    if raw is None:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Could not retrieve preprocessed data.")

    try:
        df = pd.read_csv(io.BytesIO(raw))
    except Exception as exc:
        logger.exception("Could not parse preprocessed artifact for workspace={}", workspace_id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Could not parse preprocessed data.") from exc

    preview_df = df.head(limit).astype(object).where(pd.notnull(df.head(limit)), None)
    rows = [
        {key: (value.item() if hasattr(value, "item") else value) for key, value in row.items()}
        for row in preview_df.to_dict(orient="records")
    ]
    return PreprocessedDataResponse(
        workspace_id=workspace_id,
        target_column=workspace.get("target_column"),
        columns=[str(column) for column in df.columns],
        rows=rows,
        row_count=int(len(df)),
        file_url=str(csv_url),
    )


# ——————————————————————————————————————————————————————————————
# Endpoints — predict


@router.post(
    "/{workspace_id}/api/predict",
    summary="External model API: predict on raw rows with a workspace model",
    response_model=PredictResponse,
)
@router.post(
    "/{workspace_id}/predict",
    summary="Predict on new rows using this workspace's saved fitted pipeline + model",
    response_model=PredictResponse,
)
async def predict(
    workspace_id: str,
    request: PredictRequest = Body(...),
    current_user: AuthUserRead = Depends(get_current_user),
) -> PredictResponse:
    """
    Replay the workspace's saved preprocessing pipeline (persisted during
    the most recent ``/build/structured`` call — see
    :meth:`WorkspaceService.save_pipeline_artifact`) against
    ``request.rows``, reusing every strategy's training-time fitted
    parameters instead of re-fitting scalers/encoders from the new rows,
    then runs the requested (or workspace-recommended) fitted model.

    ``request.rows`` should be shaped like the workspace's original raw
    input — i.e. exactly what you'd upload as a CSV/connector source —
    not already preprocessed.

    :raises 404: workspace not found.
    :raises 400: no rows provided.
    :raises 409: no saved pipeline / fitted model yet (run ``/build``
        first), or ``model_key`` doesn't match any fitted model.
    """
    workspace_svc = _workspace_service()
    try:
        await run_in_threadpool(workspace_svc.get_workspace, workspace_id, current_user.id)
    except WorkspaceNotFoundError:
        raise _not_found(workspace_id)

    if not request.rows:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide at least one row to predict on.",
        )

    new_data = pd.DataFrame(request.rows)

    try:
        result = await run_in_threadpool(
            workspace_svc.predict, workspace_id, new_data, request.model_key, current_user.id
        )
    except RuntimeError as exc:
        # No saved pipeline / model yet, or an unresolvable model_key — all
        # "you haven't built (this model) yet" conditions, so 409 rather
        # than 500.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except Exception as exc:
        logger.exception("Prediction failed for workspace={}", workspace_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Prediction failed: {exc}",
        )

    return PredictResponse(workspace_id=workspace_id, **result)


# ——————————————————————————————————————————————————————————————
# Endpoints — download output artifacts (models, processed CSVs)


@router.get(
    "/{workspace_id}/download",
    summary="Download an output artifact (e.g. a fitted model) for a workspace",
)
async def download_output_file(
    workspace_id: str,
    file_url: str,
    current_user: AuthUserRead = Depends(get_current_user),
) -> Response:
    """
    Stream a previously produced output artifact back to the caller.

    ``file_url`` must be one of the ``output_file_urls`` recorded against
    this workspace's runs (preprocessing or model-building) — this keeps
    the endpoint from being used to fetch arbitrary S3 objects belonging to
    other workspaces.
    """
    workspace_svc = _workspace_service()
    try:
        await run_in_threadpool(workspace_svc.get_workspace, workspace_id, current_user.id)
    except WorkspaceNotFoundError:
        raise _not_found(workspace_id)

    records = await run_in_threadpool(
        workspace_svc.get_records_by_workspace_id, workspace_id, None, 100, current_user.id
    )
    allowed_urls = {
        url for record in records for url in (record.get("output_file_urls") or [])
    }
    if file_url not in allowed_urls:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found for this workspace.",
        )

    data = await run_in_threadpool(workspace_svc.download_output_file, file_url)
    if data is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to download file from storage.",
        )

    filename = file_url.rsplit("/", 1)[-1]
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post(
    "/{workspace_id}/sources/connector",
    summary="Attach one or more tables (optionally column-restricted) from a connector to a workspace",
    response_model=List[DataSource],
)
async def add_connector_sources(
    workspace_id: str,
    request: ConnectorSourcesRequest = Body(...),
    current_user: AuthUserRead = Depends(get_current_user),
) -> List[DataSource]:
    workspace_svc = _workspace_service()
    try:
        await run_in_threadpool(workspace_svc.get_workspace, workspace_id, current_user.id)
    except WorkspaceNotFoundError:
        raise _not_found(workspace_id)

    try:
        await run_in_threadpool(
            workspace_svc.add_connector_sources,
            workspace_id,
            request.connector_id,
            [t.model_dump() for t in request.tables],
            current_user.id,
        )
    except ConnectorNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Connector '{request.connector_id}' not found.")
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    refreshed = await run_in_threadpool(workspace_svc.get_workspace, workspace_id, current_user.id)
    return [DataSource(**s) for s in (refreshed.get("input_sources") or [])]


@router.get(
    "/{workspace_id}/sources/{source_id}/preview",
    summary="Re-preview an uploaded source's columns and sample rows",
)
async def preview_source(
    workspace_id: str,
    source_id: str,
    current_user: AuthUserRead = Depends(get_current_user),
) -> Dict[str, Any]:
    workspace_svc = _workspace_service()
    try:
        await run_in_threadpool(workspace_svc.get_workspace, workspace_id, current_user.id)
    except WorkspaceNotFoundError:
        raise _not_found(workspace_id)

    try:
        return await run_in_threadpool(
            workspace_svc.preview_upload_source, workspace_id, source_id, 10, current_user.id
        )
    except SourceNotFoundError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Source '{source_id}' not found on this workspace."
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    except NotImplementedError as exc:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, str(exc))


@router.patch(
    "/{workspace_id}/sources/{source_id}/columns",
    summary="Narrow (or reset) which columns of an attached source are used",
    response_model=List[DataSource],
)
async def update_source_columns(
    workspace_id: str,
    source_id: str,
    request: SourceColumnsRequest = Body(...),
    current_user: AuthUserRead = Depends(get_current_user),
) -> List[DataSource]:
    workspace_svc = _workspace_service()
    try:
        await run_in_threadpool(workspace_svc.get_workspace, workspace_id, current_user.id)
    except WorkspaceNotFoundError:
        raise _not_found(workspace_id)

    try:
        await run_in_threadpool(
            workspace_svc.update_source_columns, workspace_id, source_id, request.columns, current_user.id
        )
    except SourceNotFoundError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Source '{source_id}' not found on this workspace."
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    refreshed = await run_in_threadpool(workspace_svc.get_workspace, workspace_id, current_user.id)
    return [DataSource(**s) for s in (refreshed.get("input_sources") or [])]