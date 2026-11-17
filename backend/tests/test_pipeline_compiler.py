"""Tests for the constrained ETL pipeline compiler."""
from __future__ import annotations

import pytest

from squirrel.core.config import settings
from squirrel.schemas.etl import PipelineDefinition
from squirrel.services.pipelines.compiler import (
    PipelineCompiler,
    PipelineValidationError,
    quote_identifier,
)


# ——————————————————————————————————————————————————————————————
# Helpers


def _definition(steps: list[dict], materialize: dict | None = None) -> PipelineDefinition:
    payload: dict = {"steps": steps}
    if materialize is not None:
        payload["materialize"] = materialize
    return PipelineDefinition.model_validate(payload)


def _source(step_id: str = "src", table_name: str = "etl_source", **extra) -> dict:
    return {
        "id": step_id,
        "type": "source",
        "source": {"kind": "table", "table_name": table_name, **extra},
    }


def _sink(step_id: str = "out", input_id: str = "src", **sink) -> dict:
    return {
        "id": step_id,
        "type": "sink",
        "input": input_id,
        "sink": {"kind": "postgres", "table": "etl_out", **sink},
    }


def _transform(step_id: str, input_id: str, **transform) -> dict:
    return {
        "id": step_id,
        "type": "transform",
        "input": input_id,
        "transform": transform,
    }


def _compile(steps: list[dict], schemas: dict | None = None):
    definition = _definition(steps)
    return PipelineCompiler().compile(
        definition,
        schemas or {"src": ["id", "name", "amount"]},
    )


# ——————————————————————————————————————————————————————————————
# Identifiers


def test_quote_identifier_rejects_unsafe_names() -> None:
    assert quote_identifier("orders") == '"orders"'
    assert quote_identifier("Order_ID") == '"Order_ID"'

    for bad in ("orders; drop table x", "bad-name", "1orders", "", "a" * 64):
        with pytest.raises(PipelineValidationError):
            quote_identifier(bad)


# ——————————————————————————————————————————————————————————————
# Happy paths


def test_source_to_sink_compiles_and_renders() -> None:
    compiled = _compile([_source(), _sink()])

    sql, params = compiled.render({"src": "staging_src"})

    assert compiled.sink_columns == ["id", "name", "amount"]
    assert params == {}
    assert sql.startswith("WITH ")
    assert '"staging_src"' in sql
    assert '"etl_step_out"' not in sql
    assert 'SELECT * FROM "etl_step_src"' in sql


def test_source_column_subset_is_projected() -> None:
    compiled = _compile(
        [_source(columns=["id", "amount"]), _sink()],
    )

    sql, _ = compiled.render({"src": "staging_src"})

    assert compiled.sink_columns == ["id", "amount"]
    assert 'SELECT "id", "amount" FROM "staging_src"' in sql
    assert '"name"' not in sql


def test_filter_value_is_parameterised() -> None:
    compiled = _compile(
        [
            _source(),
            _transform("f", "src", op="filter", column="amount", comparison="gt", value=10),
            _sink(input_id="f"),
        ]
    )

    sql, params = compiled.render({"src": "staging_src"})

    assert params == {"etl_f_0": 10}
    assert "%(etl_f_0)s" in sql
    assert "amount" in sql
    assert " 10" not in sql


def test_filter_in_uses_a_parameter_per_value() -> None:
    compiled = _compile(
        [
            _source(),
            _transform(
                "f",
                "src",
                op="filter",
                column="name",
                comparison="in",
                values=["alice", "bob"],
            ),
            _sink(input_id="f"),
        ]
    )

    sql, params = compiled.render({"src": "staging_src"})

    assert params == {"etl_f_0": "alice", "etl_f_1": "bob"}
    assert "IN (%(etl_f_0)s, %(etl_f_1)s)" in sql


def test_contains_uses_ilike_and_wraps_value() -> None:
    compiled = _compile(
        [
            _source(),
            _transform(
                "f",
                "src",
                op="filter",
                column="name",
                comparison="contains",
                value="ali",
            ),
            _sink(input_id="f"),
        ]
    )

    sql, params = compiled.render({"src": "staging_src"})

    assert params == {"etl_f_0": "%ali%"}
    assert "ILIKE %(etl_f_0)s" in sql


def test_fill_null_is_parameterised() -> None:
    compiled = _compile(
        [
            _source(),
            _transform(
                "f",
                "src",
                op="fill_null",
                fill_values={"name": "unknown", "amount": 0},
            ),
            _sink(input_id="f"),
        ]
    )

    sql, params = compiled.render({"src": "staging_src"})

    assert params == {"etl_f_0": "unknown", "etl_f_1": 0}
    assert 'COALESCE("name", %(etl_f_0)s) AS "name"' in sql
    assert 'COALESCE("amount", %(etl_f_1)s) AS "amount"' in sql


def test_rename_chain_is_allowed() -> None:
    compiled = _compile(
        [
            _source(),
            _transform("r", "src", op="rename", mapping={"id": "pk", "name": "label"}),
            _sink(input_id="r"),
        ]
    )

    sql, _ = compiled.render({"src": "staging_src"})

    assert compiled.sink_columns == ["pk", "label", "amount"]
    assert '"id" AS "pk"' in sql
    assert '"name" AS "label"' in sql


def test_cast_projects_the_same_columns() -> None:
    compiled = _compile(
        [
            _source(),
            _transform("c", "src", op="cast", casts={"amount": "numeric"}),
            _sink(input_id="c"),
        ]
    )

    sql, _ = compiled.render({"src": "staging_src"})

    assert compiled.sink_columns == ["id", "name", "amount"]
    assert 'CAST("amount" AS NUMERIC) AS "amount"' in sql


def test_dedupe_on_subset_keeps_first_row() -> None:
    compiled = _compile(
        [
            _source(),
            _transform("d", "src", op="dedupe", columns=["name"]),
            _sink(input_id="d"),
        ]
    )

    sql, _ = compiled.render({"src": "staging_src"})

    assert "ROW_NUMBER() OVER (PARTITION BY \"name\")" in sql
    assert "__etl_rn = 1" in sql


def test_limit_is_applied() -> None:
    compiled = _compile(
        [
            _source(),
            _transform("l", "src", op="limit", count=25),
            _sink(input_id="l"),
        ]
    )

    sql, _ = compiled.render({"src": "staging_src"})

    assert "LIMIT 25" in sql


def test_global_aggregate_needs_no_group_by() -> None:
    compiled = _compile(
        [
            _source(),
            _transform(
                "a",
                "src",
                op="aggregate",
                aggregations=[{"function": "count", "alias": "n"}],
            ),
            _sink(input_id="a"),
        ]
    )

    sql, _ = compiled.render({"src": "staging_src"})

    assert compiled.sink_columns == ["n"]
    assert 'COUNT(*) AS "n"' in sql
    assert "GROUP BY" not in sql


def test_grouped_aggregate_projects_group_keys() -> None:
    compiled = _compile(
        [
            _source(),
            _transform(
                "a",
                "src",
                op="aggregate",
                group_by=["name"],
                aggregations=[{"function": "sum", "column": "amount", "alias": "total"}],
            ),
            _sink(input_id="a"),
        ]
    )

    sql, _ = compiled.render({"src": "staging_src"})

    assert compiled.sink_columns == ["name", "total"]
    assert 'SUM("amount") AS "total"' in sql
    assert 'GROUP BY "name"' in sql


def test_join_projects_left_and_non_key_right_columns() -> None:
    compiled = _compile(
        [
            _source("left", table_name="etl_left"),
            _source("right", table_name="etl_right"),
            _transform(
                "j",
                "left",
                op="join",
                right="right",
                on=[{"left": "id", "right": "id"}],
                how="left",
            ),
            _sink(input_id="j"),
        ],
        schemas={"left": ["id", "name"], "right": ["id", "amount"]},
    )

    sql, _ = compiled.render({"left": "staging_left", "right": "staging_right"})

    assert compiled.sink_columns == ["id", "name", "amount"]
    assert "LEFT JOIN" in sql
    assert 'l."id" = r."id"' in sql
    assert 'r."id"' not in sql.split("ON")[0]


def test_minio_sink_with_allowed_prefix_compiles() -> None:
    compiled = _compile(
        [
            _source(),
            _sink(
                input_id="src",
                kind="minio",
                table=None,
                object_key="etl/exports/out.csv",
            ),
        ]
    )

    assert compiled.sink.kind == "minio"
    assert compiled.sink.object_key == "etl/exports/out.csv"


def test_allow_listed_sink_table_bypasses_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ETL_ALLOWED_SINK_TABLES", "custom_out")

    definition = _definition([_source(), _sink(table="custom_out")])
    PipelineCompiler().validate_structure(definition)


# ——————————————————————————————————————————————————————————————
# Structural rejections


def test_pipeline_requires_exactly_one_sink() -> None:
    with pytest.raises(PipelineValidationError, match="exactly one sink"):
        PipelineCompiler().validate_structure(_definition([_source()]))

    with pytest.raises(PipelineValidationError, match="exactly one sink"):
        PipelineCompiler().validate_structure(
            _definition([_source(), _sink(), _sink(step_id="out2")])
        )


def test_cycle_is_rejected() -> None:
    steps = [
        _source(),
        _transform("a", "b", op="limit", count=1),
        _transform("b", "a", op="limit", count=1),
        _sink(input_id="a"),
    ]

    with pytest.raises(PipelineValidationError, match="cycle"):
        PipelineCompiler().validate_structure(_definition(steps))


def test_unused_step_is_rejected() -> None:
    steps = [
        _source("src"),
        _source("extra", table_name="etl_extra"),
        _sink(input_id="src"),
    ]

    with pytest.raises(PipelineValidationError, match="not connected to the sink"):
        PipelineCompiler().validate_structure(_definition(steps))


def test_unknown_input_is_rejected() -> None:
    with pytest.raises(PipelineValidationError, match="unknown input"):
        PipelineCompiler().validate_structure(
            _definition([_source(), _sink(input_id="missing")])
        )


def test_unknown_column_is_rejected() -> None:
    with pytest.raises(PipelineValidationError, match="unknown column"):
        _compile(
            [
                _source(),
                _transform(
                    "f", "src", op="filter", column="missing", comparison="eq", value=1
                ),
                _sink(input_id="f"),
            ]
        )


def test_join_duplicate_output_columns_are_rejected() -> None:
    with pytest.raises(PipelineValidationError, match="duplicate column"):
        _compile(
            [
                _source("left", table_name="etl_left"),
                _source("right", table_name="etl_right"),
                _transform(
                    "j",
                    "left",
                    op="join",
                    right="right",
                    on=[{"left": "id", "right": "id"}],
                ),
                _sink(input_id="j"),
            ],
            schemas={"left": ["id", "name"], "right": ["id", "name"]},
        )


def test_rename_collision_is_rejected() -> None:
    with pytest.raises(PipelineValidationError, match="duplicate column"):
        _compile(
            [
                _source(),
                _transform("r", "src", op="rename", mapping={"id": "name"}),
                _sink(input_id="r"),
            ]
        )


def test_duplicate_source_columns_are_rejected() -> None:
    with pytest.raises(PipelineValidationError, match="duplicate columns"):
        _compile(
            [_source(columns=["id", "id"]), _sink()],
        )


# ——————————————————————————————————————————————————————————————
# Destination guardrails


def test_reserved_sink_table_is_rejected() -> None:
    with pytest.raises(PipelineValidationError, match="platform-owned"):
        PipelineCompiler().validate_structure(
            _definition([_source(), _sink(table="jobs")])
        )


def test_sink_table_prefix_is_enforced() -> None:
    with pytest.raises(PipelineValidationError, match="must start with 'etl_'"):
        PipelineCompiler().validate_structure(
            _definition([_source(), _sink(table="custom_out")])
        )


def test_unsafe_sink_table_identifier_is_rejected() -> None:
    with pytest.raises(PipelineValidationError, match="Invalid SQL identifier"):
        PipelineCompiler().validate_structure(
            _definition([_source(), _sink(table="etl_bad-name")])
        )


def test_minio_object_key_prefix_is_enforced() -> None:
    with pytest.raises(PipelineValidationError, match="must start with 'etl/'"):
        PipelineCompiler().validate_structure(
            _definition(
                [
                    _source(),
                    _sink(
                        kind="minio",
                        table=None,
                        object_key="exports/out.csv",
                    ),
                ]
            )
        )


def test_minio_object_key_rejects_parent_traversal() -> None:
    with pytest.raises(PipelineValidationError, match="Invalid MinIO object key"):
        PipelineCompiler().validate_structure(
            _definition(
                [
                    _source(),
                    _sink(
                        kind="minio",
                        table=None,
                        object_key="etl/../secret.csv",
                    ),
                ]
            )
        )


def test_render_requires_a_staging_table_for_each_source() -> None:
    compiled = _compile([_source(), _sink()])

    with pytest.raises(PipelineValidationError, match="No staging table"):
        compiled.render({})
