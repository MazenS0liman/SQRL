"""
Chat Route
==========

Overview
--------

This module defines the API routes for handling chat interactions in SQRL.
It provides endpoints for sending messages, retrieving chat history, and managing conversations.

Each ``POST /`` call follows this pipeline:

1. Forward the request to :class:`OrchestratorService`, which classifies the
   request (``chat`` / ``explore`` / ``build``) and runs the matching pipeline.
2. Persist the full chat turn — user message, file URLs, assistant reply,
   agent output, token counts, and response time — to PostgreSQL.
3. Return the :class:`AgentResponse` to the caller.

.. note::
    ``request.fileUrls`` are expected to already be resolved ``s3://`` URLs.
    Files are uploaded to MinIO by a dedicated upload endpoint *before* the
    chat request is sent; this route never receives raw file blobs and does
    not perform any upload step itself.

Endpoints
---------

- ``POST /``  — send a message and receive an assistant reply.
- ``WS  /ws`` — stream orchestrator observations over a websocket.

"""
# ——————————————————————————————————————————————————————————————
# Imports

# Standard Libraries
from typing import Optional

# FastAPI
from fastapi import APIRouter, Body, HTTPException, WebSocket, WebSocketDisconnect, status

# Models
from squirrel.schemas.api import AgentRequest, AgentResponse

# Services
from squirrel.services.orchestrator.OrchestratorService import OrchestratorService
from squirrel.services.chat.MessagingService import MessagingService

# Logging
from loguru import logger

# API Router
router = APIRouter()

# ——————————————————————————————————————————————————————————————
# Endpoints


@router.post(
    "",
    summary="Send a message to the chat endpoint",
    status_code=status.HTTP_200_OK,
    description=(
        "Send a message to the chat endpoint and receive a response from the "
        "assistant. ``fileUrls`` must already be resolved s3:// URLs (upload "
        "files via the dedicated upload endpoint first); the full chat turn, "
        "including the agent's output, is persisted to PostgreSQL."
    ),
    response_description=(
        "The response from the assistant, including any generated content or files."
    ),
    response_model=AgentResponse,
)
async def chat(
    request: AgentRequest = Body(
        ...,
        description=(
            "The chat request payload containing the user's message and "
            "optional parameters. fileUrls must already be s3:// URLs."
        ),
    )
) -> AgentResponse:
    """
    Chat Endpoint
    -------------

    **Pipeline:**

    1. Invoke the orchestrator with ``request.fileUrls`` as-is (already
       resolved ``s3://`` URLs) and await the assistant reply.
    2. Persist the full turn (request + response + file URLs + agent output)
       to PostgreSQL.
    3. Return the :class:`AgentResponse`.

    :param request: Structured chat request (JSON body). ``fileUrls`` must
        already be ``s3://`` URLs.
    :type request: AgentRequest

    :return: Assistant response.
    :rtype: AgentResponse

    :raises HTTPException: 500 if the orchestrator fails unexpectedly.
    """
    persistence  = MessagingService()
    orchestrator = OrchestratorService()

    # ------------------------------------------------------------------ #
    # Step 1 — Orchestrate the chat request
    # ------------------------------------------------------------------ #
    try:
        response: AgentResponse = await orchestrator.handle_request(request)
    except Exception:
        logger.exception(
            "Orchestrator failed for conversation={}", request.conversationId
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while processing your request.",
        )

    # ------------------------------------------------------------------ #
    # Step 2 — Persist the chat turn to PostgreSQL
    # ------------------------------------------------------------------ #
    try:
        persistence.save_chat_turn(
            request=request,
            response=response,
            file_urls=request.fileUrls or [],
        )
    except Exception:
        logger.exception(
            "Failed to persist chat turn for conversation={}", request.conversationId
        )

    return response


@router.websocket("/ws")
async def chat_websocket(websocket: WebSocket) -> None:
    """
    Accept a websocket connection, run each incoming chat message through
    :class:`OrchestratorService`, and stream back the assistant's response.

    .. note::
        ``fileUrls`` in each incoming payload must already be resolved
        ``s3://`` URLs — no upload step is performed here, matching the
        ``POST /`` endpoint's contract.

    .. note::
        :class:`OrchestratorService` does not currently expose a dedicated
        streaming entry point — each message is processed via the same
        :meth:`OrchestratorService.handle_request` used by the ``POST /``
        endpoint, and the resulting :class:`AgentResponse` is sent back as a
        single JSON frame per message rather than token-by-token streaming.

    :param websocket: The websocket connection managed by FastAPI.
    :type websocket: WebSocket
    """
    await websocket.accept()
    orchestrator = OrchestratorService()
    persistence  = MessagingService()

    try:
        while True:
            payload = await websocket.receive_json()

            try:
                request = AgentRequest(**payload)
            except Exception:
                logger.exception("Invalid chat payload received over websocket.")
                await websocket.send_json(
                    {"error": "Invalid request payload."}
                )
                continue

            try:
                response: AgentResponse = await orchestrator.handle_request(
                    request, websocket=websocket
                )
            except Exception:
                logger.exception(
                    "Orchestrator failed for conversation={}",
                    request.conversationId,
                )
                await websocket.send_json(
                    {"error": "An error occurred while processing your request."}
                )
                continue

            try:
                persistence.save_chat_turn(
                    request=request,
                    response=response,
                    file_urls=request.fileUrls or [],
                )
            except Exception:
                logger.exception(
                    "Failed to persist chat turn for conversation={}",
                    request.conversationId,
                )

            await websocket.send_json(response.model_dump())

    except WebSocketDisconnect:
        logger.info("Chat websocket disconnected.")
        return