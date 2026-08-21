#!/usr/bin/python
"""
Model Build Route
=================

Exposes the tabular-data model-building pipeline as a FastAPI endpoint.

Pipeline
--------
1. Download every ``s3://`` input file (processed CSV) from MinIO to a local
   temp file. Any number of files may be provided — each becomes its own
   input source.
2. Inspect every source individually and infer how they relate to one
   another (:meth:`TabularDataInspectorAgent.inspect_multi`).
3. Merge all sources into a single training frame, grounded in that
   relationship (:meth:`TabularDataProcessorAgent.run_multi`), then clean /
   transform the merged frame.
4. Run :class:`TabularDataModelBuilderAgent` (plan → execute → summarize) on
   the merged, processed frame.
5. Upload every fitted estimator as a ``.joblib`` file back to MinIO via
   :class:`WorkspaceService`.
6. Insert a workspace row that links the run to the conversation and stores
   all S3 URLs + the agent summary.
7. Return a :class:`ModelBuildResponse` with the summary and model URLs.

Endpoints
---------
- ``POST /model-build``                                      — run the full pipeline.
- ``GET  /model-build/{workspace_id}``                       — retrieve a past run.
- ``GET  /model-build/{workspace_id}/download/{model_index}``— download a fitted model.
"""
# ——————————————————————————————————————————————————————————————
# Imports

# Standard Libraries
import time
import json
from pathlib import Path
from typing import Dict

# Third-Part Libraries
import pandas as pd

# FastAPI
from fastapi import APIRouter, Depends, HTTPException, Path as FPath, Query, status
from fastapi.responses import Response

# Utils
from squirrel.core.utils import download_files, cleanup

# Agents
from squirrel.modules.agents import (
    TabularDataInspectorAgent,
    TabularDataProcessorAgent,
    TabularDataModelBuilderAgent
)

# Services
from squirrel.services.workspace.WorkspaceService import (
    WorkspaceService,
    DuplicateColumnError,
)
from squirrel.services.storage.blob.MinIOService import MinIOService

# Schemas
from squirrel.schemas.api import (
    AgentRequest,
    AgentResponse,
    LLMTokens
)
from squirrel.schemas.workspace import (
    WorkspaceRecord,
    WorkspaceListResponse,
)
from squirrel.api.routes.auth import get_current_user
from squirrel.schemas.auth import AuthUserRead

# Logging
from loguru import logger

# ——————————————————————————————————————————————————————————————
# Router

router = APIRouter()

# ——————————————————————————————————————————————————————————————
# Endpoints

@router.post(
    "",
    summary="Run the model-building pipeline on one or more processed datasets",
    status_code=status.HTTP_200_OK,
    description=(
        "Downloads every CSV in fileUrls from MinIO (each becomes its own input "
        "source), infers how multiple sources relate and merges them, trains and "
        "evaluates models with TabularDataModelBuilderAgent, uploads each fitted "
        "estimator back to MinIO, and records the run in the workspaces table."
    ),
    response_description="Model comparison summary and S3 URLs of fitted model files.",
    response_model=AgentResponse,
)
async def build_models(
    request: AgentRequest,
    current_user: AuthUserRead = Depends(get_current_user),
) -> AgentResponse:
    """
    Model-Building Pipeline Endpoint
    --------------------------------

    Accepts a :class:`ModelBuildRequest` and returns a
    :class:`ModelBuildResponse` containing the agent's model comparison
    summary, the S3 URLs of the persisted ``.joblib`` files, and the
    workspace row ``id``.

    ``fileUrls`` may contain more than one file. When it does, every file is
    treated as its own input source: sources are inspected individually,
    a relationship between them is inferred (grounded in ``targetColumn``
    and ``query``), and they are merged into a single training frame before
    preprocessing and model building proceed exactly as they would for a
    single file.

    The ``fileUrls`` field should be set to the ``outputFileUrls`` returned
    by the preprocessing endpoint so the two pipelines chain naturally.

    :param request: Agent request payload.
    :raises HTTPException 400: If no valid input files could be downloaded,
        or the target column isn't present in any of them.
    :raises HTTPException 409: If multiple sources can't be merged
        unambiguously (duplicate columns or no defensible relationship).
    :raises HTTPException 500: On unexpected pipeline failures.
    """
    minio     = MinIOService()
    workspace = WorkspaceService(minio=minio)
    start_ms  = int(time.time() * 1000)

    local_paths: list[str] = []
    target_column = request.body.targetColumn

    try:
        # ── 1. Download every input file from MinIO ─────────────────
        local_paths = await download_files(
            file_urls=request.fileUrls
        )
        if not local_paths:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="None of the provided fileUrls could be downloaded from MinIO.",
            )

        # ── 2. Load each file into its own DataFrame ──────────────────────────
        dataframes: Dict[str, pd.DataFrame] = {}
        for idx, local_path in enumerate(local_paths):
            source_id = f"source_{idx}"
            try:
                dataframes[source_id] = pd.read_csv(local_path)
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Failed to parse '{Path(local_path).name}': {exc}",
                )

        if not any(target_column in df.columns for df in dataframes.values()):
            available_columns = sorted({c for df in dataframes.values() for c in df.columns})
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Target column '{target_column}' not found in any of the "
                    f"{len(dataframes)} provided file(s). "
                    f"Available columns: {available_columns}"
                ),
            )

        # ── 3. Inspect every source + infer how they relate ───────────────────
        inspection_agent = TabularDataInspectorAgent()
        inspection: Dict = json.loads(
            inspection_agent.inspect_multi(
                dataframes=dataframes,
                objective=request.query or "",
            )
        )

        # ── 4. Persist to MinIO + Postgres via WorkspaceService ───────────────
        prompt_tokens     = getattr(inspection_agent.model, "last_input_tokens",  0) if inspection_agent.model else 0
        completion_tokens = getattr(inspection_agent.model, "last_output_tokens", 0) if inspection_agent.model else 0
        total_tokens      = prompt_tokens + completion_tokens
        response_time_ms  = int(time.time() * 1000) - start_ms

        inspection_persistence = workspace.save_run(
            workspace_id=request.workspaceId,
            agent_type="inspection",
            input_file_urls=request.fileUrls,
            data=None,
            summary=inspection,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            response_time_ms=response_time_ms,
            owner_user_id=current_user.id,
            conversation_id=request.conversationId,
        )

        logger.info(
            "Inspection done  conversation={}  workspace={}  outputs={}  n_sources={}",
            request.conversationId,
            inspection_persistence.get("record_id"),
            inspection_persistence.get("output_file_urls"),
            len(dataframes),
        )

        # ── 5. Merge sources (if needed) + preprocess data ────────────────────
        preprocess_agent = TabularDataProcessorAgent()
        relationship = inspection.get("relationship") or {}

        try:
            processed_df, preprocess_summary = preprocess_agent.run_multi(
                dataframes=dataframes,
                target_column=target_column,
                eda_summary=inspection,
                objective=request.query or "",
                merge_recommendation=relationship,
            )
        except DuplicateColumnError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": (
                        "These columns appear in more than one provided file, so "
                        "they can't be merged unambiguously. Rename or remove the "
                        "duplicate column(s), or describe how the files relate in "
                        "'query', then try again."
                    ),
                    "conflicting_columns": exc.columns,
                },
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"message": str(exc)},
            )

        # ── 6. Persist to MinIO + Postgres via WorkspaceService ───────────────
        prompt_tokens     = getattr(preprocess_agent.model, "last_input_tokens",  0) if preprocess_agent.model else 0
        completion_tokens = getattr(preprocess_agent.model, "last_output_tokens", 0) if preprocess_agent.model else 0
        total_tokens      = prompt_tokens + completion_tokens
        response_time_ms  = int(time.time() * 1000) - start_ms

        preprocess_persistence = workspace.save_run(
            workspace_id=request.workspaceId,
            agent_type="preprocessing",
            input_file_urls=request.fileUrls,
            data=processed_df,
            summary=preprocess_summary,
            prompt_tokens=prompt_tokens,
            total_tokens=total_tokens,
            response_time_ms=response_time_ms,
            owner_user_id=current_user.id,
            conversation_id=request.conversationId,
        )

        logger.info(
            "Preprocessing done  conversation={}  workspace={}  outputs={}",
            request.conversationId,
            preprocess_persistence.get("record_id"),
            preprocess_persistence.get("output_file_urls"),
        )

        # ── 7. Run model-building agent ───────────────────────────────────────
        model_builder_agent = TabularDataModelBuilderAgent(
            target_column=target_column
        )
        fitted_models, model_build_summary = model_builder_agent.run(
            data=processed_df,
            preprocessing_summary=preprocess_summary or {},
            objective=request.objective or request.query or (
                "Select the most appropriate models for this dataset and task type."
            ),
        )

        # ── 8. Persist to MinIO + Postgres via WorkspaceService ───────────────
        prompt_tokens     = getattr(model_builder_agent.model, "last_input_tokens",  0) if model_builder_agent.model else 0
        completion_tokens = getattr(model_builder_agent.model, "last_output_tokens", 0) if model_builder_agent.model else 0
        total_tokens      = prompt_tokens + completion_tokens
        response_time_ms  = int(time.time() * 1000) - start_ms

        model_build_persistence = workspace.save_run(
            workspace_id=request.workspaceId,
            agent_type="model_building",
            input_file_urls=preprocess_persistence.get("output_file_urls", []),
            data=fitted_models,
            summary=model_build_summary,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            response_time_ms=response_time_ms,
            owner_user_id=current_user.id,
            conversation_id=request.conversationId,
        )

        # ── 9. Determine human-readable reply ────────────────────────────────
        best_model  = model_build_summary.get("best_model", "")
        rationale   = model_build_summary.get("best_model_rationale", "")
        assessment  = model_build_summary.get("overall_assessment", "")

        if best_model:
            reply = (
                f"Model building completed. Best model: {best_model}. "
                f"{rationale or ''} {assessment or ''}"
            ).strip()
        else:
            reply = assessment or "Model building completed."

        logger.info(
            "Model building done  conversation={}  workspace={}  models={}",
            request.conversationId,
            model_build_persistence.get("record_id"),
            list(fitted_models.keys()),
        )

        return AgentResponse(
            reply=reply,
            action="build",
            workspaceId=request.workspaceId,
            conversationId=request.conversationId,
            body={
              "inspect": {
                    "details": inspection
                },
              "preprocess": {
                  "details": preprocess_summary,
                  "fileURLs": preprocess_persistence.get("output_file_urls", [])
                },
              "build": {
                  "details": model_build_summary,
                  "fileURLs": model_build_persistence.get("output_file_urls", []),
              }
            },
            llmTokens=LLMTokens(
                promptTokens=prompt_tokens,
                completionTokens=completion_tokens,
                totalTokens=total_tokens,
            ),
            responseTime=response_time_ms,
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Unexpected error during model building: {}", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Model-building pipeline error: {exc}",
        )
    finally:
        cleanup(local_paths)


@router.get(
    "/{workspace_id}",
    summary="Retrieve a model-building workspace by ID",
    status_code=status.HTTP_200_OK,
    response_model=WorkspaceRecord,
)
async def get_model_building_workspace(
    workspace_id: int = FPath(..., description="Primary key of the workspace row."),
    current_user: AuthUserRead = Depends(get_current_user),
) -> WorkspaceRecord:
    """
    Return the workspace record for a past model-building run.

    :param workspace_id: Primary key returned by the model-build endpoint.
    :raises HTTPException 404: If no matching workspace is found.
    """
    workspace = WorkspaceService()
    row = workspace.get_record(workspace_id, current_user.id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace {workspace_id} not found.",
        )
    return WorkspaceRecord(**row)


@router.get(
    "/{workspace_id}/download/{model_index}",
    summary="Download a fitted model file from a model-building workspace",
    status_code=status.HTTP_200_OK,
    responses={
        200: {
            "content": {"application/octet-stream": {}},
            "description": "Raw joblib model file.",
        },
        404: {"description": "Workspace or model index not found."},
    },
)
async def download_model(
    workspace_id: int = FPath(..., description="Workspace primary key."),
    model_index:  int = FPath(
        ...,
        ge=0,
        description="Zero-based index into the workspace's outputFileUrls list.",
    ),
    current_user: AuthUserRead = Depends(get_current_user),
) -> Response:
    """
    Stream a ``.joblib`` model file back to the caller as raw bytes.

    The ``model_index`` corresponds to the position in the
    ``outputFileUrls`` list returned by the model-build endpoint.  Index 0
    is the first (and usually best) model.

    :param workspace_id: Workspace primary key.
    :param model_index: Zero-based index into ``output_file_urls``.
    :raises HTTPException 404: If the workspace or index is invalid.
    :raises HTTPException 502: If the file cannot be downloaded from MinIO.
    """
    workspace_svc = WorkspaceService()
    row = workspace_svc.get_workspace(workspace_id, current_user.id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace {workspace_id} not found.",
        )

    output_urls: list[str] = row.get("output_file_urls", [])
    if model_index >= len(output_urls):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Model index {model_index} out of range "
                f"(workspace has {len(output_urls)} output file(s))."
            ),
        )

    s3_url  = output_urls[model_index]
    content = workspace_svc.download_output_file(s3_url)
    if content is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not download model from MinIO: {s3_url}",
        )

    filename = Path(s3_url).name
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(content)),
        },
    )