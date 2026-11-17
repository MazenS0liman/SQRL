#!/usr/bin/python
"""
Pipeline Compiler
=================

Validates a :class:`~squirrel.schemas.etl.PipelineDefinition` and compiles it
into a single, parameterised PostgreSQL ``SELECT``.

Design rules
------------
- **No arbitrary code.** The DSL is a closed set of structured operations; the
  compiler is the only thing that writes SQL, and it quotes every identifier.
- **Schema-aware.** Source columns are resolved by the executor before
  compilation, then propagated through each transform. Unknown columns are a
  validation error rather than a database error halfway through a run.
- **Parameterised values.** Filter/limit/aggregate values are emitted as
  psycopg named parameters, never interpolated into SQL text.
- **Explicit destinations.** Sinks are checked against the configured
  allow-list / prefix and can never target platform-owned tables.
"""
# ——————————————————————————————————————————————————————————————
# Imports

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from loguru import logger

from squirrel.constants.etl import (
    AggregateFn,
    CastType,
    FilterOp,
    JoinHow,
    RESERVED_SINK_TABLES,
    RESERVED_SOURCE_PREFIXES,
    SinkKind,
    SourceKind,
    StepType,
    TransformOp,
)
from squirrel.schemas.etl import (
    Aggregation,
    PipelineDefinition,
    PipelineStep,
    SinkSpec,
    SourceSpec,
    TransformSpec,
)


class PipelineValidationError(ValueError):
    """Raised when a pipeline definition is structurally or semantically invalid."""


_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_FILTER_SQL = {
    FilterOp.EQ: "=",
    FilterOp.NE: "<>",
    FilterOp.GT: ">",
    FilterOp.GTE: ">=",
    FilterOp.LT: "<",
    FilterOp.LTE: "<=",
    FilterOp.LIKE: "LIKE",
}

_CAST_SQL = {
    CastType.TEXT: "TEXT",
    CastType.INTEGER: "INTEGER",
    CastType.BIGINT: "BIGINT",
    CastType.NUMERIC: "NUMERIC",
    CastType.DOUBLE: "DOUBLE PRECISION",
    CastType.BOOLEAN: "BOOLEAN",
    CastType.DATE: "DATE",
    CastType.TIMESTAMPTZ: "TIMESTAMPTZ",
}

_AGGREGATE_SQL = {
    AggregateFn.COUNT: "COUNT",
    AggregateFn.SUM: "SUM",
    AggregateFn.AVG: "AVG",
    AggregateFn.MIN: "MIN",
    AggregateFn.MAX: "MAX",
}

_JOIN_SQL = {
    JoinHow.INNER: "INNER JOIN",
    JoinHow.LEFT: "LEFT JOIN",
    JoinHow.RIGHT: "RIGHT JOIN",
    JoinHow.FULL: "FULL OUTER JOIN",
}


# ——————————————————————————————————————————————————————————————
# Identifier helpers

def quote_identifier(name: str) -> str:
    """Quote a single SQL identifier, rejecting anything that is not one."""
    if not isinstance(name, str) or not _IDENTIFIER_RE.match(name) or len(name) > 63:
        raise PipelineValidationError(
            f"Invalid SQL identifier {name!r}. Identifiers must match "
            "[A-Za-z_][A-Za-z0-9_]* and be at most 63 characters."
        )
    return f'"{name}"'


def _cte_name(step_id: str) -> str:
    return f"etl_step_{step_id}"


def _param(step_id: str, index: int) -> str:
    return f"etl_{step_id}_{index}"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PipelineValidationError(message)


# ——————————————————————————————————————————————————————————————
# Compiled representation

@dataclass
class CompiledStep:
    """One compiled source/transform step."""

    id: str
    type: str
    columns: List[str]
    sql: str = ""
    params: Dict[str, Any] = field(default_factory=dict)
    source: Optional[SourceSpec] = None


@dataclass
class CompiledPipeline:
    """A validated DAG plus everything needed to render its final SELECT."""

    definition: PipelineDefinition
    sources: List[CompiledStep]
    transforms: List[CompiledStep]
    sink_step: PipelineStep
    sink: SinkSpec
    sink_columns: List[str]

    def render(self, source_tables: Dict[str, str]) -> Tuple[str, Dict[str, Any]]:
        """
        Build the final ``WITH ... SELECT ...`` statement.

        :param source_tables: Mapping of source step id to the staging table
            that the executor materialised for it.
        """
        params: Dict[str, Any] = {}
        ctes: List[str] = []

        for step in self.sources:
            _require(
                step.id in source_tables,
                f"No staging table was provided for source step '{step.id}'.",
            )
            table = source_tables[step.id]
            columns = ", ".join(quote_identifier(c) for c in step.columns)
            ctes.append(
                f"{quote_identifier(_cte_name(step.id))} AS "
                f"(SELECT {columns} FROM {quote_identifier(table)})"
            )

        for step in self.transforms:
            params.update(step.params)
            ctes.append(
                f"{quote_identifier(_cte_name(step.id))} AS ({step.sql})"
            )

        final_cte = _cte_name(self.sink_step.input or "")
        sql = "WITH " + ", ".join(ctes) + f" SELECT * FROM {quote_identifier(final_cte)}"
        return sql, params


# ——————————————————————————————————————————————————————————————
# Compiler

class PipelineCompiler:
    """Validate a definition and compile it to a parameterised SELECT."""

    def __init__(self, max_steps: int = 100) -> None:
        self._max_steps = max_steps

    # ── Public API ────────────────────────────────────────────────────────

    def validate_structure(self, definition: PipelineDefinition) -> None:
        """Validate DAG shape and sink destinations without source metadata."""
        steps = definition.steps
        _require(len(steps) <= self._max_steps, f"Pipeline has too many steps (max {self._max_steps}).")

        ids: Dict[str, PipelineStep] = {}
        for step in steps:
            _require(step.id not in ids, f"Duplicate step id '{step.id}'.")
            ids[step.id] = step

        sinks = [s for s in steps if s.type == StepType.SINK]
        _require(len(sinks) == 1, "A pipeline must have exactly one sink step.")

        for step in steps:
            self._validate_step_payload(step)

        for step in steps:
            if step.type == StepType.SOURCE:
                _require(step.input is None, f"Source step '{step.id}' cannot have an input.")
                continue
            _require(step.input is not None, f"Step '{step.id}' requires an input.")
            _require(step.input in ids, f"Step '{step.id}' references unknown input '{step.input}'.")
            _require(step.input != step.id, f"Step '{step.id}' cannot reference itself.")

        # Join right-hand references must exist and must not be self-referential.
        for step in steps:
            if step.transform and step.transform.op == TransformOp.JOIN:
                right = step.transform.right
                _require(bool(right), f"Join step '{step.id}' requires a right input.")
                _require(right in ids, f"Join step '{step.id}' references unknown right input '{right}'.")
                _require(right != step.id, f"Join step '{step.id}' cannot join itself.")

        self._topological_order(steps)  # raises on cycles
        self._validate_sink(sinks[0].sink)
        outputs = definition.outputs
        if outputs and (outputs.dashboard or outputs.ml):
            _require(
                sinks[0].sink is not None and sinks[0].sink.kind == SinkKind.POSTGRES,
                "Dashboards and ML models read the result from Postgres, so the "
                "sink must be a Postgres table.",
            )
        if outputs and outputs.ml:
            for column in [*([outputs.ml.target] if outputs.ml.target else []), *outputs.ml.features]:
                quote_identifier(column)
        self._validate_reachability(ids, sinks[0].id)

    def compile(
        self,
        definition: PipelineDefinition,
        source_schemas: Dict[str, Sequence[str]],
    ) -> CompiledPipeline:
        """
        Validate and compile *definition*.

        :param source_schemas: Columns available from each source step, keyed
            by step id. The executor resolves these from the connector/file/
            app table before compiling so unknown columns fail fast.
        """
        self.validate_structure(definition)

        order = self._topological_order(definition.steps)
        by_id = {step.id: step for step in definition.steps}
        schemas: Dict[str, List[str]] = {}
        sources: List[CompiledStep] = []
        transforms: List[CompiledStep] = []

        for step in order:
            if step.type == StepType.SOURCE:
                columns = self._source_columns(step, source_schemas)
                schemas[step.id] = columns
                sources.append(
                    CompiledStep(
                        id=step.id,
                        type=step.type,
                        columns=columns,
                        source=step.source,
                    )
                )
                continue

            _require(step.input in schemas, f"Step '{step.id}' has no compiled input.")
            input_columns = schemas[step.input]
            if step.type == StepType.TRANSFORM:
                compiled = self._compile_transform(step, input_columns, schemas)
                schemas[step.id] = compiled.columns
                transforms.append(compiled)
            # The sink's input schema is recorded below.

        sink_step = next(s for s in definition.steps if s.type == StepType.SINK)
        sink_columns = schemas[sink_step.input or ""]
        ml = definition.outputs.ml if definition.outputs else None
        if ml:
            referenced = ([ml.target] if ml.target else []) + list(ml.features)
            missing = [c for c in referenced if c not in sink_columns]
            _require(
                not missing,
                f"ML output references column(s) {missing} that the pipeline does not "
                f"produce. Available: {sink_columns}",
            )
            _require(
                not (ml.target and ml.target in ml.features),
                "The ML target column must not also be listed as a feature.",
            )
        return CompiledPipeline(
            definition=definition,
            sources=sources,
            transforms=transforms,
            sink_step=sink_step,
            sink=sink_step.sink,  # type: ignore[arg-type]
            sink_columns=sink_columns,
        )

    def schema_map(
        self,
        definition: PipelineDefinition,
        source_schemas: Dict[str, Sequence[str]],
    ) -> Dict[str, List[str]]:
        """
        Columns produced by every step, without requiring a sink/reachability.

        The editor uses this while a DAG is still incomplete (for example, a
        newly added source that has not been wired in yet). It applies the same
        transform rules as :meth:`compile`, so the columns shown in the UI match
        what a run will actually produce.
        """
        order = self._topological_order(definition.steps)
        schemas: Dict[str, List[str]] = {}

        for step in order:
            if step.type == StepType.SOURCE:
                schemas[step.id] = self._source_columns(step, source_schemas)
                continue

            if step.type == StepType.TRANSFORM:
                _require(
                    step.input in schemas,
                    f"Step '{step.id}' has no compiled input.",
                )
                compiled = self._compile_transform(
                    step, schemas[step.input], schemas
                )
                schemas[step.id] = compiled.columns
                continue

            if step.input in schemas:
                schemas[step.id] = list(schemas[step.input])

        return schemas

    # ── Structure ─────────────────────────────────────────────────────────

    @staticmethod
    def _validate_step_payload(step: PipelineStep) -> None:
        if step.type == StepType.SOURCE:
            _require(step.source is not None, f"Source step '{step.id}' is missing its source spec.")
            _require(step.transform is None and step.sink is None,
                     f"Source step '{step.id}' cannot carry a transform or sink.")
            PipelineCompiler._validate_source_spec(step.id, step.source)
        elif step.type == StepType.TRANSFORM:
            _require(step.transform is not None, f"Transform step '{step.id}' is missing its transform spec.")
            _require(step.source is None and step.sink is None,
                     f"Transform step '{step.id}' cannot carry a source or sink.")
        else:
            _require(step.sink is not None, f"Sink step '{step.id}' is missing its sink spec.")
            _require(step.source is None and step.transform is None,
                     f"Sink step '{step.id}' cannot carry a source or transform.")

    @staticmethod
    def _validate_source_spec(step_id: str, source: SourceSpec) -> None:
        if source.kind == SourceKind.CONNECTOR:
            _require(bool(source.connector_id), f"Source step '{step_id}' requires connector_id.")
            _require(bool(source.table), f"Source step '{step_id}' requires table.")
        elif source.kind == SourceKind.FILE:
            _require(bool(source.file_url), f"Source step '{step_id}' requires file_url.")
        elif source.kind == SourceKind.TABLE:
            _require(bool(source.table_name), f"Source step '{step_id}' requires table_name.")
        for column in source.columns:
            quote_identifier(column)

    def _validate_sink(self, sink: Optional[SinkSpec]) -> None:
        _require(sink is not None, "Sink step is missing its sink spec.")
        assert sink is not None
        if sink.kind == SinkKind.POSTGRES:
            _require(bool(sink.table), "Postgres sink requires a table name.")
            self._validate_sink_table(sink.table or "")
        elif sink.kind == SinkKind.MINIO:
            _require(bool(sink.object_key), "MinIO sink requires an object_key.")
            self._validate_object_key(sink.object_key or "")

    @staticmethod
    def _validate_sink_table(table: str) -> None:
        quote_identifier(table)
        if table in RESERVED_SINK_TABLES:
            raise PipelineValidationError(
                f"'{table}' is a platform-owned table and cannot be an ETL destination."
            )
        if table.startswith(RESERVED_SOURCE_PREFIXES):
            raise PipelineValidationError(
                f"'{table}' uses a reserved platform prefix and cannot be an ETL destination."
            )

        from squirrel.core.config import settings

        allowed = settings.etl_allowed_sink_tables
        if allowed:
            if table not in allowed:
                raise PipelineValidationError(
                    f"Destination '{table}' is not in ETL_ALLOWED_SINK_TABLES."
                )
            return

        prefix = settings.ETL_SINK_TABLE_PREFIX
        if not table.startswith(prefix):
            raise PipelineValidationError(
                f"Destination '{table}' must start with '{prefix}'. Set "
                "ETL_ALLOWED_SINK_TABLES to allow other explicit names."
            )

    @staticmethod
    def _validate_object_key(object_key: str) -> None:
        from squirrel.core.config import settings

        if object_key.startswith("/") or ".." in object_key.split("/"):
            raise PipelineValidationError(
                f"Invalid MinIO object key {object_key!r}."
            )
        prefix = settings.ETL_MINIO_PREFIX
        if not object_key.startswith(prefix):
            raise PipelineValidationError(
                f"MinIO object key must start with '{prefix}'."
            )

    @staticmethod
    def _validate_reachability(ids: Dict[str, PipelineStep], sink_id: str) -> None:
        """Reject steps that cannot contribute to the single sink."""
        reachable: set[str] = set()
        stack = [sink_id]
        while stack:
            current = stack.pop()
            if current in reachable:
                continue
            reachable.add(current)
            step = ids.get(current)
            if step is None:
                continue
            if step.input:
                stack.append(step.input)
            if (
                step.transform
                and step.transform.op == TransformOp.JOIN
                and step.transform.right
            ):
                stack.append(step.transform.right)

        unused = [step.id for step in ids.values() if step.id not in reachable]
        if unused:
            raise PipelineValidationError(
                f"Step(s) {unused} are not connected to the sink. Remove them or "
                "wire them into the pipeline."
            )

    @staticmethod
    def _topological_order(steps: List[PipelineStep]) -> List[PipelineStep]:
        """Kahn's algorithm; raises on cycles. Stable by definition order."""
        by_id = {step.id: step for step in steps}
        indegree = {step.id: 0 for step in steps}
        edges: Dict[str, List[str]] = {step.id: [] for step in steps}

        for step in steps:
            refs = []
            if step.input:
                refs.append(step.input)
            if step.transform and step.transform.op == TransformOp.JOIN and step.transform.right:
                refs.append(step.transform.right)
            for ref in refs:
                if ref in by_id:
                    edges[ref].append(step.id)
                    indegree[step.id] += 1

        ready = [step.id for step in steps if indegree[step.id] == 0]
        ordered: List[PipelineStep] = []
        while ready:
            current = ready.pop(0)
            ordered.append(by_id[current])
            for child in edges[current]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)

        if len(ordered) != len(steps):
            raise PipelineValidationError("Pipeline definition contains a cycle.")
        return ordered

    # ── Sources ───────────────────────────────────────────────────────────

    @staticmethod
    def _source_columns(
        step: PipelineStep,
        source_schemas: Dict[str, Sequence[str]],
    ) -> List[str]:
        if step.id not in source_schemas:
            raise PipelineValidationError(
                f"Source step '{step.id}' could not be resolved — no columns were "
                "provided. Check the connector/file/table reference."
            )
        available = list(source_schemas[step.id])
        for column in available:
            quote_identifier(column)
        _require(len(set(available)) == len(available),
                 f"Source step '{step.id}' has duplicate column names: {available}.")

        source = step.source
        assert source is not None
        if source.columns:
            _require(
                len(set(source.columns)) == len(source.columns),
                f"Source step '{step.id}' contains duplicate columns.",
            )
            missing = [c for c in source.columns if c not in available]
            if missing:
                raise PipelineValidationError(
                    f"Source step '{step.id}' references unknown column(s) {missing}. "
                    f"Available: {available}"
                )
            return list(source.columns)
        return available

    # ── Transforms ────────────────────────────────────────────────────────

    def _compile_transform(
        self,
        step: PipelineStep,
        input_columns: List[str],
        schemas: Dict[str, List[str]],
    ) -> CompiledStep:
        spec = step.transform
        assert spec is not None
        op = spec.op

        if op == TransformOp.SELECT:
            columns = self._require_columns(spec.columns, input_columns, step.id, "select")
            sql = (
                f"SELECT {', '.join(quote_identifier(c) for c in columns)} "
                f"FROM {quote_identifier(_cte_name(step.input or ''))}"
            )
            return CompiledStep(step.id, step.type, columns, sql=sql)

        if op == TransformOp.FILTER:
            return self._compile_filter(step, input_columns)

        if op == TransformOp.FILL_NULL:
            fills = spec.fill_values or {}
            _require(bool(fills), f"Fill_null step '{step.id}' requires fill_values.")
            params: Dict[str, Any] = {}
            names: Dict[str, str] = {}
            for i, (column, value) in enumerate(fills.items()):
                _require(column in input_columns,
                         f"Fill_null step '{step.id}' references unknown column '{column}'.")
                _require(value is not None and not isinstance(value, (dict, list)),
                         f"Fill_null step '{step.id}' needs a scalar replacement for '{column}'.")
                names[column] = _param(step.id, i)
                params[names[column]] = value
            projection = ", ".join(
                f"COALESCE({quote_identifier(c)}, %({names[c]})s) AS {quote_identifier(c)}"
                if c in fills else quote_identifier(c)
                for c in input_columns
            )
            sql = f"SELECT {projection} FROM {quote_identifier(_cte_name(step.input or ''))}"
            return CompiledStep(step.id, step.type, list(input_columns), sql=sql, params=params)

        if op == TransformOp.RENAME:
            mapping = spec.mapping or {}
            _require(bool(mapping), f"Rename step '{step.id}' requires a non-empty mapping.")
            for old, new in mapping.items():
                _require(old in input_columns,
                         f"Rename step '{step.id}' references unknown column '{old}'.")
                quote_identifier(new)
            output = [mapping.get(column, column) for column in input_columns]
            duplicates = sorted({c for c in output if output.count(c) > 1})
            if duplicates:
                raise PipelineValidationError(
                    f"Rename step '{step.id}' would create duplicate column(s) "
                    f"{duplicates}."
                )
            projection = ", ".join(
                f"{quote_identifier(column)} AS {quote_identifier(mapping[column])}"
                if column in mapping else quote_identifier(column)
                for column in input_columns
            )
            sql = (
                f"SELECT {projection} FROM {quote_identifier(_cte_name(step.input or ''))}"
            )
            return CompiledStep(step.id, step.type, output, sql=sql)

        if op == TransformOp.CAST:
            casts = spec.casts or {}
            _require(bool(casts), f"Cast step '{step.id}' requires a non-empty casts mapping.")
            for column, target in casts.items():
                _require(column in input_columns,
                         f"Cast step '{step.id}' references unknown column '{column}'.")
                _require(target in CastType.ALL,
                         f"Cast step '{step.id}' has unsupported type '{target}'.")
            projection = ", ".join(
                f"CAST({quote_identifier(c)} AS {_CAST_SQL[casts[c]]}) AS {quote_identifier(c)}"
                if c in casts else quote_identifier(c)
                for c in input_columns
            )
            sql = (
                f"SELECT {projection} FROM {quote_identifier(_cte_name(step.input or ''))}"
            )
            return CompiledStep(step.id, step.type, list(input_columns), sql=sql)

        if op == TransformOp.DEDUPE:
            subset = spec.columns or []
            if subset:
                self._require_columns(subset, input_columns, step.id, "dedupe")
                partition = ", ".join(quote_identifier(c) for c in subset)
                inner = (
                    f"SELECT *, ROW_NUMBER() OVER (PARTITION BY {partition}) AS __etl_rn "
                    f"FROM {quote_identifier(_cte_name(step.input or ''))}"
                )
                projection = ", ".join(quote_identifier(c) for c in input_columns)
                sql = (
                    f"SELECT {projection} FROM ({inner}) AS __etl_dedupe "
                    f"WHERE __etl_rn = 1"
                )
            else:
                sql = (
                    f"SELECT DISTINCT * FROM {quote_identifier(_cte_name(step.input or ''))}"
                )
            return CompiledStep(step.id, step.type, list(input_columns), sql=sql)

        if op == TransformOp.LIMIT:
            _require(spec.count is not None, f"Limit step '{step.id}' requires count.")
            sql = (
                f"SELECT * FROM {quote_identifier(_cte_name(step.input or ''))} "
                f"LIMIT {int(spec.count)}"
            )
            return CompiledStep(step.id, step.type, list(input_columns), sql=sql)

        if op == TransformOp.AGGREGATE:
            return self._compile_aggregate(step, input_columns)

        if op == TransformOp.JOIN:
            return self._compile_join(step, input_columns, schemas)

        raise PipelineValidationError(f"Unsupported transform op '{op}'.")

    def _compile_filter(self, step: PipelineStep, input_columns: List[str]) -> CompiledStep:
        spec = step.transform
        assert spec is not None
        _require(bool(spec.column), f"Filter step '{step.id}' requires a column.")
        _require(bool(spec.comparison), f"Filter step '{step.id}' requires a comparison.")
        column = spec.column or ""
        _require(column in input_columns,
                 f"Filter step '{step.id}' references unknown column '{column}'.")
        comparison = spec.comparison or ""
        col_sql = quote_identifier(column)
        params: Dict[str, Any] = {}

        if comparison in FilterOp.NULLARY:
            operator = "IS NULL" if comparison == FilterOp.IS_NULL else "IS NOT NULL"
            predicate = f"{col_sql} {operator}"
        elif comparison in FilterOp.LIST:
            values = spec.values or []
            _require(bool(values), f"Filter step '{step.id}' requires a non-empty values list.")
            placeholders = []
            for index, value in enumerate(values):
                name = _param(step.id, index)
                params[name] = value
                placeholders.append(f"%({name})s")
            operator = "IN" if comparison == FilterOp.IN else "NOT IN"
            predicate = f"{col_sql} {operator} ({', '.join(placeholders)})"
        else:
            _require(spec.value is not None,
                     f"Filter step '{step.id}' requires a value for comparison '{comparison}'.")
            name = _param(step.id, 0)
            params[name] = (
                f"%{spec.value}%" if comparison == FilterOp.CONTAINS else spec.value
            )
            if comparison == FilterOp.CONTAINS:
                predicate = f"{col_sql} ILIKE %({name})s"
            else:
                predicate = f"{col_sql} {_FILTER_SQL[comparison]} %({name})s"

        sql = (
            f"SELECT * FROM {quote_identifier(_cte_name(step.input or ''))} "
            f"WHERE {predicate}"
        )
        return CompiledStep(step.id, step.type, list(input_columns), sql=sql, params=params)

    def _compile_aggregate(self, step: PipelineStep, input_columns: List[str]) -> CompiledStep:
        spec = step.transform
        assert spec is not None
        group_by = spec.group_by or []
        aggregations = spec.aggregations or []
        _require(bool(aggregations), f"Aggregate step '{step.id}' requires aggregations.")
        if group_by:
            self._require_columns(group_by, input_columns, step.id, "aggregate")

        aliases: List[str] = []
        projections: List[str] = []
        for column in group_by:
            projections.append(quote_identifier(column))
            aliases.append(column)

        for aggregation in aggregations:
            alias = aggregation.alias
            quote_identifier(alias)
            _require(alias not in aliases,
                     f"Aggregate step '{step.id}' has duplicate output column '{alias}'.")
            function = aggregation.function
            if function == AggregateFn.COUNT and not aggregation.column:
                expression = "COUNT(*)"
            else:
                _require(bool(aggregation.column),
                         f"Aggregate '{alias}' requires a column.")
                assert aggregation.column is not None
                _require(aggregation.column in input_columns,
                         f"Aggregate step '{step.id}' references unknown column "
                         f"'{aggregation.column}'.")
                expression = (
                    f"{_AGGREGATE_SQL[function]}({quote_identifier(aggregation.column)})"
                )
            projections.append(f"{expression} AS {quote_identifier(alias)}")
            aliases.append(alias)

        sql = f"SELECT {', '.join(projections)} FROM {quote_identifier(_cte_name(step.input or ''))}"
        if group_by:
            sql += " GROUP BY " + ", ".join(quote_identifier(c) for c in group_by)
        return CompiledStep(step.id, step.type, aliases, sql=sql)

    def _compile_join(
        self,
        step: PipelineStep,
        left_columns: List[str],
        schemas: Dict[str, List[str]],
    ) -> CompiledStep:
        spec = step.transform
        assert spec is not None
        right = spec.right or ""
        _require(right in schemas,
                 f"Join step '{step.id}' right input '{right}' has not been compiled yet.")
        right_columns = schemas[right]
        keys = spec.on or []
        _require(bool(keys), f"Join step '{step.id}' requires at least one join key.")
        how = spec.how or JoinHow.INNER
        _require(how in JoinHow.ALL, f"Join step '{step.id}' has unsupported join type '{how}'.")

        predicates: List[str] = []
        right_key_columns = set()
        for key in keys:
            _require(key.left in left_columns,
                     f"Join step '{step.id}' references unknown left column '{key.left}'.")
            _require(key.right in right_columns,
                     f"Join step '{step.id}' references unknown right column '{key.right}'.")
            right_key_columns.add(key.right)
            predicates.append(
                f"l.{quote_identifier(key.left)} = r.{quote_identifier(key.right)}"
            )

        output = list(left_columns) + [c for c in right_columns if c not in right_key_columns]
        duplicates = sorted({c for c in output if output.count(c) > 1})
        if duplicates:
            raise PipelineValidationError(
                f"Join step '{step.id}' would produce duplicate column(s) {duplicates}. "
                "Rename them or drop the duplicate via a preceding 'select' step."
            )

        projection = [f"l.{quote_identifier(c)}" for c in left_columns]
        projection += [
            f"r.{quote_identifier(c)}"
            for c in right_columns
            if c not in right_key_columns
        ]
        sql = (
            f"SELECT {', '.join(projection)} "
            f"FROM {quote_identifier(_cte_name(step.input or ''))} AS l "
            f"{_JOIN_SQL[how]} {quote_identifier(_cte_name(right))} AS r "
            f"ON {' AND '.join(predicates)}"
        )
        return CompiledStep(step.id, step.type, output, sql=sql)

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _require_columns(
        columns: Optional[List[str]],
        available: List[str],
        step_id: str,
        operation: str,
    ) -> List[str]:
        _require(bool(columns), f"'{operation}' step '{step_id}' requires a non-empty columns list.")
        assert columns is not None
        for column in columns:
            quote_identifier(column)
        missing = [c for c in columns if c not in available]
        if missing:
            raise PipelineValidationError(
                f"'{operation}' step '{step_id}' references unknown column(s) {missing}. "
                f"Available: {available}"
            )
        _require(len(set(columns)) == len(columns),
                 f"'{operation}' step '{step_id}' contains duplicate columns.")
        return list(columns)
