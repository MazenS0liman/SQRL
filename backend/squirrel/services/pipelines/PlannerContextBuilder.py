#!/usr/bin/python
"""
Planner Context Builder
=======================

Resolves the sources a user mentioned (connector tables, CSV files, app tables)
into real column lists *before* the LLM is called, so the planner works from
facts instead of guessing column names from the request text.

Everything goes through ``PipelineService.resolve_source_columns`` so the
planner sees exactly what the compiler will later validate against (same
ownership checks, same allow-lists, same CSV header normalisation).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger

from squirrel.constants.etl import SourceKind
from squirrel.schemas.etl import SourceSpec
from squirrel.services.pipelines.compiler import PipelineValidationError

_MAX_LISTED_TABLES = 100


class PlannerContextBuilder:
    """Build the grounding context passed to :class:`ETLPlannerAgent`."""

    def __init__(self, pipeline_service: Any) -> None:
        self._svc = pipeline_service

    def build(
        self,
        owner_user_id: Optional[str],
        *,
        connector_id: Optional[str] = None,
        table: Optional[str] = None,
        file_urls: Optional[List[str]] = None,
        table_names: Optional[List[str]] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        :return: A context dict with ``available_sources`` (resolved columns),
            ``unresolved_sources`` (why a reference could not be read) and, when
            a connector was given without a table, ``available_tables``.
        """
        context: Dict[str, Any] = dict(extra or {})
        available: List[Dict[str, Any]] = []
        unresolved: List[Dict[str, Any]] = []

        specs: List[SourceSpec] = []
        if connector_id and table:
            specs.append(
                SourceSpec(kind=SourceKind.CONNECTOR, connector_id=connector_id, table=table)
            )
        elif connector_id:
            context["available_tables"] = self._list_tables(connector_id, owner_user_id)
        for url in file_urls or []:
            specs.append(SourceSpec(kind=SourceKind.FILE, file_url=url))
        for name in table_names or []:
            specs.append(SourceSpec(kind=SourceKind.TABLE, table_name=name))

        for spec in specs:
            ref = spec.model_dump(exclude_none=True, exclude_defaults=True)
            try:
                columns = self._svc.resolve_source_columns(spec, owner_user_id)
                available.append({**ref, "columns": columns})
            except Exception as exc:  # noqa: BLE001 - report to the LLM, don't crash
                logger.info("Planner context: could not resolve {}: {}", ref, exc)
                unresolved.append({**ref, "error": str(exc)})

        if available:
            context["available_sources"] = available
        if unresolved:
            context["unresolved_sources"] = unresolved
        return context

    def _list_tables(self, connector_id: str, owner_user_id: Optional[str]) -> List[str]:
        try:
            tables = self._svc._get_connector().list_tables(
                connector_id, owner_user_id=owner_user_id
            )
        except Exception as exc:  # noqa: BLE001
            raise PipelineValidationError(
                f"Could not list tables for connector '{connector_id}': {exc}"
            ) from exc
        return tables[:_MAX_LISTED_TABLES]
