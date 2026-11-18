"""Tests for the ETL planner grounding context builder."""
from __future__ import annotations

import pytest

from squirrel.constants.etl import SourceKind
from squirrel.schemas.etl import SourceSpec
from squirrel.services.pipelines.compiler import PipelineValidationError
from squirrel.services.pipelines.PlannerContextBuilder import (
    _MAX_LISTED_TABLES,
    PlannerContextBuilder,
)


class FakePipelineService:
    def __init__(self, columns=None, error: Exception | None = None, tables=None, tables_error=None):
        self.columns = columns or ["id", "name", "amount"]
        self.error = error
        self.tables = tables or ["public.orders", "public.customers"]
        self.tables_error = tables_error
        self.resolved: list[SourceSpec] = []
        self.listed: list[tuple[str, str | None]] = []

    def resolve_source_columns(self, spec: SourceSpec, owner_user_id):
        self.resolved.append(spec)
        if self.error is not None:
            raise self.error
        return self.columns

    def _get_connector(self):
        return self

    def list_tables(self, connector_id, owner_user_id=None):
        self.listed.append((connector_id, owner_user_id))
        if self.tables_error is not None:
            raise self.tables_error
        return self.tables


def test_build_resolves_connector_table() -> None:
    service = FakePipelineService()
    context = PlannerContextBuilder(service).build(
        "user-1", connector_id="conn-1", table="public.orders"
    )

    assert context["available_sources"] == [
        {
            "kind": SourceKind.CONNECTOR,
            "connector_id": "conn-1",
            "table": "public.orders",
            "columns": ["id", "name", "amount"],
        }
    ]
    assert "unresolved_sources" not in context
    assert service.resolved[0].connector_id == "conn-1"


def test_build_lists_tables_when_no_table_given() -> None:
    service = FakePipelineService()
    context = PlannerContextBuilder(service).build("user-1", connector_id="conn-1")

    assert context["available_tables"] == ["public.orders", "public.customers"]
    assert "available_sources" not in context
    assert service.listed == [("conn-1", "user-1")]


def test_build_resolves_files_and_app_tables() -> None:
    service = FakePipelineService(columns=["a", "b"])
    context = PlannerContextBuilder(service).build(
        None,
        file_urls=["s3://bucket/orders.csv"],
        table_names=["etl_raw_orders"],
    )

    kinds = [source["kind"] for source in context["available_sources"]]
    assert kinds == [SourceKind.FILE, SourceKind.TABLE]
    assert context["available_sources"][0]["file_url"] == "s3://bucket/orders.csv"
    assert context["available_sources"][1]["table_name"] == "etl_raw_orders"


def test_build_records_unresolved_sources_instead_of_raising() -> None:
    service = FakePipelineService(error=ValueError("column read failed"))
    context = PlannerContextBuilder(service).build(None, file_urls=["s3://bucket/bad.csv"])

    assert "available_sources" not in context
    assert context["unresolved_sources"] == [
        {
            "kind": SourceKind.FILE,
            "file_url": "s3://bucket/bad.csv",
            "error": "column read failed",
        }
    ]


def test_build_merges_extra_context() -> None:
    context = PlannerContextBuilder(FakePipelineService()).build(
        None, extra={"sink_table": "etl_out", "notes": "keep"}
    )

    assert context == {"sink_table": "etl_out", "notes": "keep"}


def test_build_does_not_mutate_extra() -> None:
    extra = {"sink_table": "etl_out"}
    PlannerContextBuilder(FakePipelineService()).build(
        None, file_urls=["s3://bucket/a.csv"], extra=extra
    )

    assert extra == {"sink_table": "etl_out"}


def test_list_tables_truncates_long_catalogs() -> None:
    service = FakePipelineService(tables=[f"table_{i}" for i in range(_MAX_LISTED_TABLES + 25)])

    context = PlannerContextBuilder(service).build(None, connector_id="conn-1")

    assert len(context["available_tables"]) == _MAX_LISTED_TABLES


def test_list_tables_wraps_connector_errors() -> None:
    service = FakePipelineService(tables_error=RuntimeError("boom"))

    with pytest.raises(PipelineValidationError, match="Could not list tables"):
        PlannerContextBuilder(service).build(None, connector_id="conn-1")
