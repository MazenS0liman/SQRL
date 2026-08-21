"""
Explore Data Route
==================

Overview
--------

This module defines the API routes for exploratory data analysis in SQRL.
It provides endpoints for generating analysis upon data.

"""
# ——————————————————————————————————————————————————————————————
# Imports

# Standard Libraries
import os
import time
import json
from datetime import datetime
from typing import Optional

# FastAPI
from fastapi import APIRouter, Body, Depends, HTTPException, WebSocket, WebSocketDisconnect, status

# Utils
from squirrel.core.utils import download_files, cleanup

# Agents
from squirrel.modules.agents import TabularDataExploratoryAgent

# Services
from squirrel.services.storage import PostgresService

# Schema
from squirrel.schemas.api import (
    AgentRequest,
    AgentResponse,
    LLMTokens
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
    summary="Run the exploratory data analysis pipeline on uploaded files",
    status_code=status.HTTP_200_OK,
    description=(
        "Downloads each input file from MinIO and perform exploratory data analysis"
        "with TabularDataExploratoryAgent"
        "and records the run in Postgres database."
    ),
    response_description="Exploratory data analysis on the data.",
    response_model=AgentResponse
)
async def explore(
    request: AgentRequest,
    current_user: AuthUserRead = Depends(get_current_user),
) -> AgentResponse:
    """
    EDA Endpoint
    ------------

    Accepts a :class:`AgentRequest` and returns a
    :class:`AgentResponse` containing the agent's findings.

    :param request: EDA request payload.
    :type request: AgentRequest

    :raises HTTPException 400: If no valid input files could be downloaded.
    :raises HTTPException 500: On unexpected pipeline failures.
    """
    # Start timer
    start_ms  = int(time.time() * 1000)
    
    try:
        # ── 1. Initialize postgres serivce ────────────────────────────────
        postgres_service = PostgresService(
            connection_string=os.environ("DATABASE_URL")
        )

        # ── 2. Download input files from MinIO ────────────────────────────────
        local_paths = await download_files(
            file_urls=request.fileUrls
        )
        
        # ── 3. Load data to Postgres ────────────────────────────────
        postgres_service.load(
            data_path=local_paths[0],
            table_name=request.body.table_name or "tmp"
        )
        
        # ── 4. Initialize exploratory agent ────────────────────────────────
        agent = TabularDataExploratoryAgent(
            postgres_service=postgres_service
        )
        
        # ── 5. Generate schema metadata for data ────────────────────────────────
        schema_metadata = postgres_service._generate_schema_metadata(table_name=request.body.table_name or "tmp")
        
        # ── 6. Perform exploratory data analysis ────────────────────────────────
        output = agent.explore(
            schema_metadata=schema_metadata
        )
        
        # ── 7. Determine human-readable reply ────────────────────────────────
        if isinstance(output, str):
            try:
                output = json.loads(output)
            except (json.JSONDecodeError, TypeError):
                logger.warning("Could not parse output as JSON: {}", output)
                output = {}
        
        if "summary" not in output or "dataset_description" not in output["summary"]:
            reply = f"Analysis failed: {output.get('error', 'Unknown error')}"
        else:
            reply = output["summary"]["dataset_description"]
            for f in output["summary"].get("key_findings", []):
                reply = "\n\n".join([reply, f["finding"]])
    
        # ── 8. Compute token counts ───────────────────────────────────────────
        prompt_tokens     = getattr(agent.model, "last_input_tokens",  0) if agent.model else 0
        completion_tokens = getattr(agent.model, "last_output_tokens", 0) if agent.model else 0
        total_tokens      = prompt_tokens + completion_tokens
        response_time_ms  = int(time.time() * 1000) - start_ms

        return AgentResponse(
            reply=reply,
            action='explore',
            projectId=request.projectId,
            conversationId=request.conversationId,
            fileUrls=request.fileUrls,
            body=output,
            timestamp=datetime.utcnow(),
            responseTime=response_time_ms,
            llmTokens=LLMTokens(
                promptTokens=int(prompt_tokens),
                completionTokens=int(completion_tokens),
                totalTokens=int(total_tokens)
            ),
            fileProcessingIssues=output.get("fileProcessingIssues", []),
            metadata={**(output.get("metadata", {}) or {}), "owner_user_id": current_user.id}
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Unexpected error during preprocessing: {}", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"EDA pipeline error: {exc}"
        )
    finally:
        cleanup(local_paths)
