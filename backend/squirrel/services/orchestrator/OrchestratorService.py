#!/usr/bin/python
"""
Orchestrator Service
====================

Coordinates chat requests end-to-end:

1. :class:`OrchestratorAgent` makes a single LLM call to classify the
   request as ``chat``, ``explore``, or ``build``.
2. The matching private method runs the full pipeline for that route.
3. Every agent run (``explore`` / ``build``) is persisted to the
   ``workspaces`` table via :class:`WorkspaceService`. ``chat`` is a plain
   LLM reply with no specialised pipeline and is not persisted there.
4. A :class:`AgentResponse` is assembled and returned.

Routes
------

``chat``
    General conversation — no file or target column required. Returns a
    plain LLM reply with no workspace persistence.

``explore``
    Hypothesis-driven EDA executed as SQL against a live PostgreSQL database.
    Requires ``fileUrls`` (the CSV is loaded into Postgres as a temp table)
    and a ``query``.  Persists one workspace row (``agent_type='explore'``).

``build``
    inspect → preprocess → train.  Requires ``fileUrls``, which may contain
    more than one file — each file is treated as its own input source. When
    multiple sources are given, they are inspected individually, a
    relationship between them is inferred, and they are merged into a single
    training frame before preprocessing/model-building proceed exactly as
    they would for a single file. The target column is resolved by the LLM
    classifier (from ``body.targetColumn`` or inferred from the query)
    before the pipeline runs.  Persists three workspace rows
    (``inspection``, ``preprocessing``, ``model_building``).
"""

from __future__ import annotations

# ──────────────────────────────────────────────────────────────────────────────
# Imports
# ──────────────────────────────────────────────────────────────────────────────

# Standard Libraries
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Third-party
import pandas as pd

# FastAPI
from fastapi import WebSocket

# Utils
from squirrel.core.utils import download_files, cleanup

# Agents
from squirrel.modules.agents import (
    OrchestratorAgent,
    TabularDataExploratoryAgent,
    TabularDataInspectorAgent,
    TabularDataProcessorAgent,
    TabularDataModelBuilderAgent,
)
from squirrel.modules.providers.abstract.IProvider import Provider

# Services
from squirrel.services.storage.database import PostgresService
from squirrel.services.workspace.WorkspaceService import (
    WorkspaceService,
    DuplicateColumnError,
)
from squirrel.services.storage.blob.MinIOService import MinIOService

# Schemas
from squirrel.schemas.api import AgentRequest, AgentResponse, LLMTokens
from squirrel.schemas.file import FileProcessingIssue

# Logging
from loguru import logger


# ──────────────────────────────────────────────────────────────────────────────
# OrchestratorService
# ──────────────────────────────────────────────────────────────────────────────


class OrchestratorService:
    """
    Route chat requests to the ``chat``, ``explore``, or ``build`` pipeline.

    :param orchestrator_agent: Optional pre-constructed
        :class:`OrchestratorAgent`.  A default instance is created when
        omitted.
    """

    def __init__(
        self,
        orchestrator_agent: Optional[OrchestratorAgent] = None,
    ) -> None:
        self._agent = orchestrator_agent or OrchestratorAgent()

    # ──────────────────────────────────────────────────────────────────────────
    # Public entry point
    # ──────────────────────────────────────────────────────────────────────────

    async def handle_request(
        self,
        request:   AgentRequest,
        websocket: Optional[WebSocket] = None,
    ) -> AgentResponse:
        """
        Process a chat request and return the final :class:`AgentResponse`.

        :param request:   Incoming agent request.
        :param websocket: Optional open websocket for streaming observations.
        :return:          Assembled agent response.
        """
        started_at = time.perf_counter()

        # ── 1. Classify the request via a single LLM call ────────────────────
        decision: Dict[str, Any] = self._agent.orchestrate(
            query=request.query or "",
            body=request.body or {},
            file_urls=request.fileUrls or [],
            metadata=getattr(request, "metadata", {}) or {},
        )

        route_key: str = decision["route_key"]
        logger.info(
            "OrchestratorService: route='{}' confidence={:.2f} conversation={}",
            route_key,
            decision["confidence"],
            request.conversationId,
        )

        # ── 2. Dispatch to the matching pipeline ──────────────────────────────
        try:
            if route_key == "build":
                output = await self._build_models(request, decision, websocket)
            elif route_key == "explore":
                output = await self._explore_data(request, websocket)
            else:  # "chat" — also the default fallback for an unrecognised route
                output = await self._chat_response(request, decision)

        except Exception as exc:
            logger.exception(
                "Pipeline '{}' raised an unhandled exception for conversation={}: {}",
                route_key,
                request.conversationId,
                exc,
            )
            output = {
                "reply": "An unexpected error occurred while processing your request.",
            }

        # ── 3. Assemble AgentResponse ─────────────────────────────────────────
        # `action` and `projectId` are always sourced directly from the route
        # key and request — never from `output` — so the fallback error dict
        # above does not need to carry them.
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)

        return AgentResponse(
            reply=output.get("reply", "I'm sorry, I couldn't process your request."),
            action=route_key,
            projectId=request.projectId,
            conversationId=request.conversationId,
            body=output.get("body"),
            responseTime=elapsed_ms,
            llmTokens=LLMTokens(
                promptTokens=int(
                    output.get("promptTokens", output.get("prompt_tokens", 0)) or 0
                ),
                completionTokens=int(
                    output.get("completionTokens", output.get("completion_tokens", 0)) or 0
                ),
                totalTokens=int(
                    output.get("totalTokens", output.get("total_tokens", 0)) or 0
                ),
            ),
            fileProcessingIssues=output.get("fileProcessingIssues", []),
            metadata={
                **(output.get("metadata") or {}),
                "route_key":    route_key,
                "agent_name":   decision.get("agent_name"),
                "confidence":   decision.get("confidence"),
                "observations": decision.get("observations", []),
            },
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Route: chat — general conversation, no specialised pipeline
    # ──────────────────────────────────────────────────────────────────────────

    async def _chat_response(
        self,
        request:  AgentRequest,
        decision: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Return a plain LLM reply for general conversation.

        No file download, no database connection, and no workspace
        persistence — this route is intentionally lightweight for greetings,
        clarifying questions, and anything that doesn't require a
        specialised data pipeline.

        :param request:  The incoming agent request.
        :param decision: The classifier's route-decision dict.
        :return: Output dict consumed by :meth:`handle_request`.
        """
        try:
            response = self._agent.get_response(
                fn_name="generate_response",
                model_input={
                    "system_prompt": (
                        "You are a helpful data-analysis assistant. Respond "
                        "conversationally and concisely to the user's message."
                    ),
                    "user_prompt": request.query or "",
                },
                strict=False,
                provider_order=[Provider.GROQ, Provider.GEMINI],
                preference_model_names=[
                    "llama-3.3-70b-versatile",
                    "models/gemini-2.5-flash",
                ],
            )

            reply = (response or "").strip() if isinstance(response, str) else ""
            if not reply:
                reply = (
                    "Hi! I can help you explore a dataset or build a predictive "
                    "model — just attach a file and let me know what you'd like "
                    "to do."
                )

            prompt_tokens     = _tokens(self._agent, "last_input_tokens")
            completion_tokens = _tokens(self._agent, "last_output_tokens")
            total_tokens      = prompt_tokens + completion_tokens

            return {
                "reply":            reply,
                "promptTokens":     prompt_tokens,
                "completionTokens": completion_tokens,
                "totalTokens":      total_tokens,
                "metadata": {
                    "agent": "GeneralPurposeAgent",
                    "task":  "chat",
                },
            }

        except Exception as exc:
            logger.exception(
                "Error in _chat_response for conversation={}: {}",
                request.conversationId,
                exc,
            )
            return {
                "reply": "I'm sorry, I couldn't process your request."
            }

    # ──────────────────────────────────────────────────────────────────────────
    # Route: build — inspect → preprocess → train
    # ──────────────────────────────────────────────────────────────────────────

    async def _build_models(
        self,
        request:   AgentRequest,
        decision:  Dict[str, Any],
        websocket: Optional[WebSocket] = None,
    ) -> Dict[str, Any]:
        """
        Full ML pipeline: inspection → merge → preprocessing → model training.

        ``request.fileUrls`` may contain more than one file. Each file is
        treated as its own input source: every source is inspected
        individually, a relationship between the sources is inferred
        (grounded in ``target_column`` and the user's query), and they are
        merged into a single training frame before preprocessing/model
        building proceed unchanged from the single-file case.

        Three workspace rows are written (one per pipeline stage).  The
        processed CSV and every fitted model are persisted to MinIO.

        :param request:  Must carry ``fileUrls`` (one or more).
        :param decision: The classifier's route-decision dict — supplies
            ``target_column`` when the LLM inferred or extracted one.
        :return: Output dict consumed by :meth:`handle_request`.
        """
        local_paths: List[str] = []

        minio     = MinIOService()
        workspace = WorkspaceService(minio=minio)
        start_ms  = int(time.time() * 1000)

        project_id      = request.projectId or ""
        conversation_id = request.conversationId or ""
        file_urls       = request.fileUrls or []
        body            = request.body or {}

        # Resolve target column: explicit body value wins, then the LLM's
        # inferred value from the classification call.
        target_column = (
            body.get("targetColumn")
            or body.get("target_column")
            or decision.get("target_column")
            or ""
        )

        try:
            # ── 1. Validate pre-conditions ────────────────────────────────────
            if not file_urls:
                return {
                    "reply": (
                        "Please attach a CSV file and specify the target column "
                        "to start the model-building pipeline."
                    )
                }
            if not target_column:
                return {
                    "reply": (
                        "I understood you want to build a predictive model, but "
                        "I couldn't determine the target column. Please specify "
                        "which column you want to predict."
                    )
                }

            # ── 2. Download every input file from MinIO ───────────────────────
            local_paths = await download_files(file_urls=file_urls)
            if not local_paths:
                return {
                    "reply": "Could not download the attached file(s) from storage."
                }

            # ── 2b. Load each file into its own DataFrame ─────────────────────
            dataframes: Dict[str, pd.DataFrame] = {}
            for idx, local_path in enumerate(local_paths):
                source_id = f"source_{idx}"
                try:
                    dataframes[source_id] = pd.read_csv(local_path)
                except Exception as exc:
                    return {"reply": f"Failed to parse '{Path(local_path).name}': {exc}"}

            if not any(target_column in df.columns for df in dataframes.values()):
                available_columns = sorted({c for df in dataframes.values() for c in df.columns})
                return {
                    "reply": (
                        f"Target column '{target_column}' was not found in any of the "
                        f"{len(dataframes)} attached file(s). "
                        f"Available columns: {available_columns}"
                    )
                }

            # ── 3. Inspect every source + infer how they relate ───────────────
            inspection_agent = TabularDataInspectorAgent()
            inspection: Dict = json.loads(
                inspection_agent.inspect_multi(
                    dataframes=dataframes,
                    objective=request.query or "",
                )
            )

            prompt_tokens     = _tokens(inspection_agent, "last_input_tokens")
            completion_tokens = _tokens(inspection_agent, "last_output_tokens")
            total_tokens      = prompt_tokens + completion_tokens

            workspace.save_run(
                project_id=project_id,
                conversation_id=conversation_id,
                agent_type="inspection",
                input_file_urls=file_urls,
                data=None,
                summary=inspection,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                response_time_ms=int(time.time() * 1000) - start_ms,
            )
            logger.info(
                "Inspection done  conversation={}  n_sources={}",
                conversation_id, len(dataframes),
            )

            # ── 4. Merge sources (if needed) + preprocess ─────────────────────
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
                return {
                    "reply": (
                        "These columns appear in more than one attached file, so "
                        f"they can't be merged unambiguously: {', '.join(exc.columns)}. "
                        "Rename or remove the duplicate column(s), or describe how "
                        "the files relate in your message, then try again."
                    ),
                    "fileProcessingIssues": _file_issues(file_urls, exc),
                }
            except ValueError as exc:
                return {
                    "reply": f"Could not combine the attached files: {exc}",
                    "fileProcessingIssues": _file_issues(file_urls, exc),
                }

            prompt_tokens     = _tokens(preprocess_agent, "last_input_tokens")
            completion_tokens = _tokens(preprocess_agent, "last_output_tokens")
            total_tokens      = prompt_tokens + completion_tokens

            preprocess_persistence = workspace.save_run(
                project_id=project_id,
                conversation_id=conversation_id,
                agent_type="preprocessing",
                input_file_urls=file_urls,
                data=processed_df,
                summary=preprocess_summary,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                response_time_ms=int(time.time() * 1000) - start_ms,
            )
            processed_urls = preprocess_persistence.get("output_file_urls", [])
            logger.info(
                "Preprocessing done  conversation={}  outputs={}",
                conversation_id,
                processed_urls,
            )

            # ── 5. Build models ───────────────────────────────────────────────
            model_agent = TabularDataModelBuilderAgent(target_column=target_column)
            fitted_models, model_summary = model_agent.run(
                data=processed_df,
                preprocessing_summary=preprocess_summary or {},
                objective=request.query or (
                    "Select the most appropriate models for this dataset and task type."
                ),
            )

            prompt_tokens     = _tokens(model_agent, "last_input_tokens")
            completion_tokens = _tokens(model_agent, "last_output_tokens")
            total_tokens      = prompt_tokens + completion_tokens

            model_persistence = workspace.save_run(
                project_id=project_id,
                conversation_id=conversation_id,
                agent_type="model_building",
                input_file_urls=processed_urls,
                data=fitted_models,
                summary=model_summary,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                response_time_ms=int(time.time() * 1000) - start_ms,
            )
            model_urls = model_persistence.get("output_file_urls", [])
            logger.info(
                "Model building done  conversation={}  models={}  outputs={}",
                conversation_id,
                list(fitted_models.keys()),
                model_urls,
            )

            # ── 6. Compose reply ──────────────────────────────────────────────
            best_model = model_summary.get("best_model", "")
            rationale  = model_summary.get("best_model_rationale", "")
            assessment = model_summary.get("overall_assessment", "")

            reply = (
                f"Model building completed. Best model: {best_model}. "
                f"{rationale} {assessment}".strip()
                if best_model
                else assessment or "Model building completed."
            )

            return {
                "reply": reply,
                "body": {
                    "inspect": {
                        "details": inspection,
                    },
                    "preprocess": {
                        "details":   preprocess_summary,
                        "fileURLs":  processed_urls,
                        "record_id": preprocess_persistence.get("record_id"),
                    },
                    "build": {
                        "details":   model_summary,
                        "fileURLs":  model_urls,
                        "record_id": model_persistence.get("record_id"),
                    },
                },
                "promptTokens":     prompt_tokens,
                "completionTokens": completion_tokens,
                "totalTokens":      total_tokens,
                "metadata": {
                    "agent":          "TabularDataModelBuilderAgent",
                    "task":           "build",
                    "target_column":  target_column,
                    "n_sources":      len(dataframes),
                    "models_trained": list(fitted_models.keys()),
                    "best_model":     best_model,
                },
            }

        except Exception as exc:
            logger.exception(
                "Error in _build_models for conversation={}: {}", conversation_id, exc
            )
            return {
                "reply": f"An error occurred during model building: {exc}",
                "fileProcessingIssues": _file_issues(file_urls, exc),
            }

        finally:
            cleanup(local_paths)

    # ──────────────────────────────────────────────────────────────────────────
    # Route: explore — hypothesis-driven DB EDA
    # ──────────────────────────────────────────────────────────────────────────

    async def _explore_data(
        self,
        request:   AgentRequest,
        websocket: Optional[WebSocket] = None,
    ) -> Dict[str, Any]:
        """
        Load every attached CSV into its own Postgres table and run
        :class:`TabularDataExploratoryAgent`.

        When more than one file is attached, each becomes its own table
        (named after the file, de-duplicated with a numeric suffix if
        needed) so the exploratory agent can reason across all of them via
        SQL joins — it already receives a schema per table, so no change to
        the agent itself is required for multiple sources.

        The full EDA result is persisted as a workspace row with
        ``agent_type='explore'``.

        :param request: Must carry ``fileUrls`` and ``query``.
        :return: Output dict consumed by :meth:`handle_request`.
        """
        local_paths: List[str] = []

        minio     = MinIOService()
        workspace = WorkspaceService(minio=minio)
        start_ms  = int(time.time() * 1000)

        project_id      = request.projectId or ""
        conversation_id = request.conversationId or ""
        file_urls       = request.fileUrls or []
        body            = request.body or {}

        try:
            # ── 1. Validate pre-conditions ────────────────────────────────────
            if not file_urls or not request.query:
                return {
                    "reply": (
                        "Please attach at least one file and include a query to "
                        "begin exploratory data analysis."
                    )
                }

            # ── 2. Download every file from MinIO ─────────────────────────────
            local_paths = await download_files(file_urls=file_urls)
            if not local_paths:
                return {
                    "reply": "Could not download the attached file(s) from storage."
                }

            # ── 3. Load each file into its own Postgres table ─────────────────
            connection_string = os.environ["DATABASE_URL"]
            postgres_service  = PostgresService(connection_string=connection_string)

            # Accept either a list ("table_names") or, for backward
            # compatibility with single-file callers, a single string
            # ("table_name") to name the first (and possibly only) source.
            requested_names = list(body.get("table_names") or [])
            if not requested_names and body.get("table_name"):
                requested_names = [body["table_name"]]

            used_names: set = set()
            table_names: List[str] = []
            # NOTE: schema_metadata must always stay keyed by table name —
            # {table_name: table_schema, ...} — for every request, whether it
            # carries one file or several. TabularDataExploratoryAgent.explore
            # (via generate_schema_details) does `for table, meta in
            # schema_metadata.items()`, treating every top-level key as a
            # table; unwrapping this to a single table's schema for the
            # one-file case would make that loop iterate over the schema's
            # own keys (e.g. "columns") instead, silently corrupting the
            # pipeline. Keeping the shape uniform is also what lets the agent
            # reason about JOINs across sources when more than one is given.
            schema_metadata: Dict[str, Any] = {}

            for idx, local_path in enumerate(local_paths):
                base_name = (
                    requested_names[idx]
                    if idx < len(requested_names)
                    else Path(local_path).stem
                )
                table_name = "".join(
                    c if c.isalnum() or c == "_" else "_" for c in base_name
                ).lower() or f"source_{idx}"

                # De-duplicate table names across multiple sources.
                candidate = table_name
                suffix = 1
                while candidate in used_names:
                    candidate = f"{table_name}_{suffix}"
                    suffix += 1
                table_name = candidate
                used_names.add(table_name)
                table_names.append(table_name)

                table_schema = postgres_service.load(
                    data_path=local_path,
                    table_name=table_name,
                    if_exists="replace",
                )
                schema_metadata[table_name] = table_schema

            # ── 4. Run EDA agent ──────────────────────────────────────────────
            agent = TabularDataExploratoryAgent(
                postgres_service=PostgresService(
                    connection_string=connection_string
                )
            )

            output: Dict = agent.explore(schema_metadata=schema_metadata)

            if isinstance(output, str):
                try:
                    output = json.loads(output)
                except (json.JSONDecodeError, TypeError):
                    logger.warning(
                        "Could not parse explore output as JSON: {}", output
                    )
                    output = {}

            # ── 5. Persist workspace row ──────────────────────────────────────
            prompt_tokens     = _tokens(agent, "last_input_tokens")
            completion_tokens = _tokens(agent, "last_output_tokens")
            total_tokens      = prompt_tokens + completion_tokens

            persistence = workspace.save_run(
                project_id=project_id,
                conversation_id=conversation_id,
                agent_type="explore",
                input_file_urls=file_urls,
                data=None,
                summary=output,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                response_time_ms=int(time.time() * 1000) - start_ms,
            )
            logger.info(
                "Explore done  conversation={}  record={}  tables={}",
                conversation_id,
                persistence.get("record_id"),
                table_names,
            )

            # ── 6. Compose reply ──────────────────────────────────────────────
            summary = output.get("summary", {})
            if not summary or "dataset_description" not in summary:
                reply = f"Exploration failed: {output.get('error', 'Unknown error')}"
            else:
                reply = summary["dataset_description"]
                for finding in summary.get("key_findings", []):
                    reply = "\n\n".join([reply, finding.get("finding", "")])

            return {
                "reply":            reply,
                "body":             output,
                "promptTokens":     prompt_tokens,
                "completionTokens": completion_tokens,
                "totalTokens":      total_tokens,
                "metadata": {
                    "agent":     "TabularDataExploratoryAgent",
                    "task":      "explore",
                    "record_id": persistence.get("record_id"),
                    "tables":    table_names,
                },
            }

        except Exception as exc:
            logger.exception(
                "Error in _explore_data for conversation={}: {}", conversation_id, exc
            )
            return {
                "reply": f"An error occurred during data exploration: {exc}",
                "fileProcessingIssues": _file_issues(file_urls, exc),
            }

        finally:
            cleanup(local_paths)


# ──────────────────────────────────────────────────────────────────────────────
# Module-level helpers (private)
# ──────────────────────────────────────────────────────────────────────────────


def _tokens(agent: Any, attr: str) -> int:
    """Safely read a token-count attribute from an agent's model."""
    model = getattr(agent, "model", None)
    return int(getattr(model, attr, 0) or 0) if model else 0


def _file_issues(
    file_urls: List[str],
    exc: Exception,
) -> List[FileProcessingIssue]:
    """Build a :class:`FileProcessingIssue` list from a list of URLs and an exception."""
    return [
        FileProcessingIssue(
            fileUrl=url,
            fileName=Path(url).name,
            issue="processing_error",
            message=str(exc),
        )
        for url in file_urls
    ]