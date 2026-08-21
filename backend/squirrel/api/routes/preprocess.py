#!/usr/bin/python
"""
Preprocess Route
================

Exposes the tabular-data preprocessing pipeline as a FastAPI endpoint.

Pipeline
--------
1. Download every ``s3://`` input file from MinIO to a local temp file. Any
   number of files may be provided — each becomes its own input source.
2. When more than one file is given, infer how the sources relate
   (:meth:`TabularDataInspectorAgent.inspect_multi`) and merge them into a
   single frame before cleaning/transforming
   (:meth:`TabularDataProcessorAgent.run_multi`). A single file skips
   straight to the normal plan → execute → summarize lifecycle.
3. Upload the processed DataFrame as a CSV back to MinIO via
   :class:`WorkspaceService`.
4. Insert a workspace row that links the run to the conversation and stores
   all S3 URLs + the agent summary.
5. Return an :class:`AgentResponse` the caller can immediately chain
   into the model-building endpoint.

Endpoints
---------
- ``POST /preprocess``   — run the preprocessing pipeline on one or more files.
- ``GET  /preprocess/{workspace_id}``  — retrieve a past preprocessing workspace.
- ``GET  /preprocess/conversation/{conversation_id}``  — list workspaces for a conversation.

"""
# ——————————————————————————————————————————————————————————————
# Imports

# Standard Libraries
import json
import time
from pathlib import Path
from typing import Dict

# Third-Party Libraries
import pandas as pd

# FastAPI
from fastapi import APIRouter, Depends, HTTPException, Path as FPath, Query, status

# Utils
from squirrel.core.utils import download_files, cleanup

# Agents
from squirrel.modules.agents import TabularDataInspectorAgent, TabularDataProcessorAgent

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
    summary="Run the preprocessing pipeline on one or more uploaded files",
    status_code=status.HTTP_200_OK,
    description=(
        "Downloads every file in fileUrls from MinIO (each becomes its own input "
        "source). When multiple files are given, infers how they relate and merges "
        "them into a single frame; cleans and transforms the result with "
        "TabularDataProcessorAgent, uploads it back to MinIO, and records the run "
        "in the workspaces table."
    ),
    response_description="Preprocessing summary and S3 URLs of output files.",
    response_model=AgentResponse,
)
async def preprocess(
    request: AgentRequest,
    current_user: AuthUserRead = Depends(get_current_user),
) -> AgentResponse:
    """
    Preprocessing Pipeline Endpoint
    -------------------------------

    Accepts an :class:`AgentRequest` and returns an :class:`AgentResponse`
    containing the agent's summary, the S3 URLs of the output CSV(s), and
    the workspace row ``id`` (under ``body.record_id``) that can be used to
    retrieve the run later.

    ``fileUrls`` may contain more than one file. ``body.targetColumn`` (or
    ``target_column``), if supplied, is used purely to ground a multi-source
    merge (excluded from the duplicate-column check, and passed to the LLM
    fallback when sources need to be joined) — it is not required for a
    single-file request.

    :param request: Preprocessing request payload. Structured options
        (``edaSummary``, ``objective``, ``targetColumn``) are read from
        ``request.body``.
    :type request: AgentRequest

    :raises HTTPException 400: If no valid input files could be downloaded.
    :raises HTTPException 409: If multiple sources can't be merged
        unambiguously (duplicate columns or no defensible relationship).
    :raises HTTPException 500: On unexpected pipeline failures.
    """
    minio     = MinIOService()
    workspace = WorkspaceService(minio=minio)
    start_ms  = int(time.time() * 1000)

    body: dict = request.body or {}
    target_column = body.get("targetColumn") or body.get("target_column")
    objective = body.get("objective") or request.query or (
        "Clean and transform the dataset to prepare it for downstream modelling."
    )

    local_paths: list[str] = []

    try:
        # ── 1. Download input files from MinIO ────────────────────────────────
        local_paths = await download_files(
            file_urls=request.fileUrls
        )
        if not local_paths:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="None of the provided fileUrls could be downloaded from MinIO.",
            )

        # ── 2. Load every file into its own DataFrame ─────────────────────────
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

        # ── 3. Run preprocessing agent (merging sources first if needed) ──────
        agent = TabularDataProcessorAgent()
        inspector_prompt_tokens = 0
        inspector_completion_tokens = 0

        if len(dataframes) > 1:
            eda_summary = body.get("edaSummary")
            if not eda_summary:
                inspector = TabularDataInspectorAgent()
                eda_summary = json.loads(
                    inspector.inspect_multi(dataframes=dataframes, objective=objective)
                )
                inspector_prompt_tokens = (
                    getattr(inspector.model, "last_input_tokens", 0) if inspector.model else 0
                )
                inspector_completion_tokens = (
                    getattr(inspector.model, "last_output_tokens", 0) if inspector.model else 0
                )
            relationship = eda_summary.get("relationship") or {}

            try:
                processed_df, summary = agent.run_multi(
                    dataframes=dataframes,
                    target_column=target_column,
                    eda_summary=eda_summary,
                    objective=objective,
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
                            "'query' / 'body.objective', then try again."
                        ),
                        "conflicting_columns": exc.columns,
                    },
                )
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"message": str(exc)},
                )
        else:
            df = next(iter(dataframes.values()))
            processed_df, summary = agent.run(
                data=df,
                eda_summary=body.get("edaSummary") or {},
                objective=objective,
            )

        # ── 4. Compute token counts (including any ad-hoc inspection call) ────
        prompt_tokens     = inspector_prompt_tokens + (
            getattr(agent.model, "last_input_tokens", 0) if agent.model else 0
        )
        completion_tokens = inspector_completion_tokens + (
            getattr(agent.model, "last_output_tokens", 0) if agent.model else 0
        )
        total_tokens      = prompt_tokens + completion_tokens
        response_time_ms  = int(time.time() * 1000) - start_ms

        # ── 5. Persist to MinIO + Postgres via WorkspaceService ───────────────
        persistence = workspace.save_run(
            workspace_id=request.workspaceId,
            agent_type="preprocessing",
            input_file_urls=request.fileUrls,
            data=processed_df,
            summary=summary,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            response_time_ms=response_time_ms,
            owner_user_id=current_user.id,
            conversation_id=request.conversationId,
        )
        record_id         = persistence.get("record_id")
        output_file_urls  = persistence.get("output_file_urls", [])

        # ── 6. Determine human-readable reply ────────────────────────────────
        if summary.get("status") == "failed":
            reply = f"Preprocessing failed: {summary.get('error', 'Unknown error')}"
        else:
            reply = summary.get(
                "overall_assessment",
                "Preprocessing completed successfully.",
            )

        logger.info(
            "Preprocessing done  conversation={}  record={}  outputs={}  n_sources={}",
            request.conversationId,
            record_id,
            output_file_urls,
            len(dataframes),
        )

        all_columns_in = sorted({c for df in dataframes.values() for c in df.columns})
        rows_in = sum(len(df) for df in dataframes.values())

        return AgentResponse(
            reply=reply,
            action="preprocess",
            projectId=request.projectId,
            conversationId=request.conversationId,
            body={
                "details":   summary,
                "fileURLs":  output_file_urls,
                "record_id": record_id,
            },
            llmTokens=LLMTokens(
                promptTokens=prompt_tokens,
                completionTokens=completion_tokens,
                totalTokens=total_tokens,
            ),
            responseTime=response_time_ms,
            metadata={
                "agent":       "TabularDataProcessorAgent",
                "task":        "preprocess",
                "n_sources":   len(dataframes),
                "rows_in":     rows_in,
                "rows_out":    len(processed_df),
                "columns_in":  all_columns_in,
                "columns_out": list(processed_df.columns),
            },
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Unexpected error during preprocessing: {}", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Preprocessing pipeline error: {exc}",
        )
    finally:
        cleanup(local_paths)


@router.get(
    "/{workspace_id}",
    summary="Retrieve a preprocessing workspace by ID",
    status_code=status.HTTP_200_OK,
    response_model=WorkspaceRecord,
)
async def get_preprocessing_workspace(
    workspace_id: int = FPath(..., description="Primary key of the workspace row."),
    current_user: AuthUserRead = Depends(get_current_user),
) -> WorkspaceRecord:
    """
    Return the workspace record for a past preprocessing run.

    :param workspace_id: Primary key returned by the preprocessing endpoint.
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
    "/conversation/{conversation_id}",
    summary="List preprocessing workspaces for a conversation",
    status_code=status.HTTP_200_OK,
    response_model=WorkspaceListResponse,
)
async def list_preprocessing_workspaces(
    conversation_id: str = FPath(..., description="Conversation identifier."),
    limit: int = Query(50, ge=1, le=200, description="Maximum rows to return."),
    current_user: AuthUserRead = Depends(get_current_user),
) -> WorkspaceListResponse:
    """
    Return all preprocessing workspace rows for *conversation_id*, newest first.

    :param conversation_id: Conversation to query.
    :param limit: Maximum number of rows.
    """
    workspace = WorkspaceService()
    rows = workspace.get_records_by_conversation_id(
        conversation_id=conversation_id,
        agent_type="preprocessing",
        limit=limit,
        owner_user_id=current_user.id,
    )
    return WorkspaceListResponse(
        total=len(rows),
        workspaces=[WorkspaceRecord(**r) for r in rows],
    )