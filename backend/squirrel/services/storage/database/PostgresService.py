"""
PostgreSQL-backed implementation of the database service interface.

This module provides :class:`PostgresService`, an implementation of
``IDatabaseService`` using :mod:`psycopg` (v3).
"""
# ——————————————————————————————————————————————————————————————
# Imports

# Standard Libraries
import re
import os
import time
from typing import Any, Optional, Dict
from typing_extensions import override

# Third-Party Libraries
import pandas as pd
from sqlalchemy import create_engine
import psycopg  # pyright: ignore[reportMissingImports]
from psycopg import sql  # pyright: ignore[reportMissingImports]
from psycopg.rows import dict_row  # pyright: ignore[reportMissingImports]

# Abstract
from squirrel.services.storage.database.IDatabaseService import IDatabaseService

# Logging
from loguru import logger

# ——————————————————————————————————————————————————————————————
# Constants

_RETRY_ATTEMPTS: int = 3
_RETRY_DELAY_SECONDS: float = 0.5

# Psycopg operational error codes that warrant a reconnect attempt.
_RECONNECT_ERRORS: frozenset[str] = frozenset(
    {
        "08000",  # connection exception
        "08003",  # connection does not exist
        "08006",  # connection failure
        "57P01",  # admin shutdown
        "57P02",  # crash shutdown
        "57P03",  # cannot connect now
    }
)

# ——————————————————————————————————————————————————————————————
# PostgreSQL Service Class
class PostgresService(IDatabaseService):
    """
    Concrete database service for PostgreSQL via *psycopg* (v3).

    The service supports two calling conventions for mutating operations:

    Examples
    --------
    .. code-block:: python

        # Raw SQL
        db.insert({"sql": "INSERT INTO t (a) VALUES (%(a)s)", "params": {"a": 1}})

        # Structured dict
        db.insert({"table": "t", "values": {"a": 1}})
        db.insert({"table": "t", "a": 1})  # flat shorthand

    All public methods handle rollback on failure and log exceptions via
    :mod:`loguru`. Connection loss is retried up to ``_RETRY_ATTEMPTS``
    times with a short back-off before re-raising.

    :param connection_string: Optional connection string or DSN. If omitted,
        the environment variables ``DATABASE_URL`` or ``POSTGRES_DSN`` are
        consulted.
    :type connection_string: Optional[str]
    :param autoconnect: If ``True``, attempts to connect during
        initialisation.
    :type autoconnect: bool
    """

    def __init__(
        self,
        connection_string: Optional[str] = None,
        autoconnect: bool = True,
    ) -> None:
        super().__init__()
        self.connection_string: str = (
            connection_string
            or os.getenv("DATABASE_URL")
            or os.getenv("POSTGRES_DSN")
            or ""
        )
        if not self.connection_string:
            raise ValueError(
                "A PostgreSQL connection string is required. "
                "Pass connection_string= or set DATABASE_URL / POSTGRES_DSN."
            )

        self._connection: Optional[Any] = None
        if autoconnect:
            self.connect()

    # ------------------------------------------------------------------
    # Internal helpers

    def _is_closed(self) -> bool:
        """Return ``True`` when there is no live connection.

        :return: ``True`` when there is no active connection.
        :rtype: bool
        """
        return self._connection is None or bool(
            getattr(self._connection, "closed", True)
        )

    def _ensure_connection(self) -> Any:
        """Return an open connection, reconnecting if necessary.

        Ensures a live connection is available and returns it.

        :return: Active database connection.
        :rtype: Any
        """
        if self._is_closed():
            self.connect()
        return self._connection

    def _generate_schema_metadata(
        self, 
        table_name: str
    ) -> dict:
        """
        Generate schema metadata for a given table in the PostgreSQL database.
        
        :param table_name: Name of the table to inspect.
        :type table_name: str
        
        :return: A dictionary containing column names, data types, and nullability.
        :rtype: dict
        """
        inspector = self._connection.cursor()
        inspector.execute(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns WHERE table_name = %s",
            (table_name,)
        )
        columns = inspector.fetchall()
        return {"columns": columns}

    def execute(
        self,
        statement: Any,
        params: Optional[dict] = None,
        fetch: bool = False,
    ) -> dict:
        """
        Execute a statement inside a cursor and optionally return rows.

        :param statement: A :mod:`psycopg.sql` composable or plain SQL string.
        :type statement: Any
        :param params: Optional mapping of named parameters for the statement
            (``%(name)s`` style).
        :type params: Optional[dict]
        :param fetch: When ``True``, fetches all rows and includes ``rows`` and
            ``columns`` in the returned mapping. When ``False``, the
            transaction is committed and only ``rowcount`` is returned.
        :type fetch: bool

        :return: Result mapping containing ``success``, ``rowcount`` and
            optionally ``rows`` and ``columns``.
        :rtype: dict

        :raises psycopg.Error: On database errors; the connection is rolled
            back before the exception is re-raised.
        """
        for attempt in range(1, _RETRY_ATTEMPTS + 1):
            connection = self._ensure_connection()
            try:
                with connection.cursor(row_factory=dict_row) as cursor:
                    cursor.execute(statement, params)

                    if fetch:
                        rows = cursor.fetchall()
                        connection.commit()
                        return {
                            "success": True,
                            "rowcount": cursor.rowcount,
                            "rows": [dict(row) for row in rows],
                            "columns": list(rows[0].keys()) if rows else [],
                        }

                    connection.commit()
                    return {"success": True, "rowcount": cursor.rowcount}

            except psycopg.OperationalError as exc:
                sqlstate = getattr(exc, "sqlstate", None) or ""
                if sqlstate in _RECONNECT_ERRORS and attempt < _RETRY_ATTEMPTS:
                    logger.warning(
                        "PostgreSQL connection lost ({}), retrying {}/{}…",
                        sqlstate,
                        attempt,
                        _RETRY_ATTEMPTS,
                    )
                    self._connection = None
                    time.sleep(_RETRY_DELAY_SECONDS * attempt)
                    continue

                self._safe_rollback(connection)
                raise

            except Exception:
                self._safe_rollback(connection)
                raise

        # Should be unreachable, but keeps type-checkers happy.
        raise RuntimeError("_execute exhausted all retry attempts without result.")

    @staticmethod
    def _safe_rollback(connection: Any) -> None:
        """Attempt a rollback without masking the original exception.

        :param connection: Connection object to rollback.
        :type connection: Any
        :return: None
        :rtype: None
        """
        try:
            connection.rollback()
        except Exception:
            logger.exception("Rollback failed")

    # ------------------------------------------------------------------
    # Statement builders

    @staticmethod
    def _build_insert_statement(table: str, values: dict) -> Any:
        """Build a parameterised INSERT statement for *table*.

        :param table: Target table name.
        :type table: str
        :param values: Mapping of column names to values.
        :type values: dict
        :return: A :class:`psycopg.sql.Composable` INSERT statement.
        :rtype: Any
        :raises ValueError: If *values* is empty.
        """

        columns = list(values.keys())
        if not columns:
            raise ValueError("Insert data must contain at least one column value.")

        return sql.SQL(
            "INSERT INTO {table} ({columns}) VALUES ({placeholders})"
        ).format(
            table=sql.Identifier(table),
            columns=sql.SQL(", ").join(sql.Identifier(col) for col in columns),
            placeholders=sql.SQL(", ").join(
                sql.SQL(f"%({col})s") for col in columns
            ),
        )

    @staticmethod
    def _build_update_statement(table: str, values: dict, where_clause: str) -> Any:
        """
        Build a parameterised UPDATE statement for *table*.

        :param table: Target table name.
        :type table: str
        :param values: Mapping of column names to values.
        :type values: dict
        :param where_clause: WHERE clause (with or without the "WHERE" keyword).
        :type where_clause: str
        :return: A :class:`psycopg.sql.Composable` UPDATE statement.
        :rtype: Any
        :raises ValueError: If *values* is empty.
        """

        columns = list(values.keys())
        if not columns:
            raise ValueError("Update data must contain at least one column value.")

        statement = sql.SQL("UPDATE {table} SET {assignments}").format(
            table=sql.Identifier(table),
            assignments=sql.SQL(", ").join(
                sql.SQL("{col} = %({param})s").format(
                    col=sql.Identifier(col),
                    param=sql.SQL(col),
                )
                for col in columns
            ),
        )

        stripped = where_clause.strip()
        if stripped:
            prefix = "" if stripped.lower().startswith("where ") else "WHERE "
            statement = statement + sql.SQL(f" {prefix}{stripped}")

        return statement

    @staticmethod
    def _extract_values(data: dict, reserved: set[str]) -> dict:
        """Return all keys in *data* that are not in *reserved*.

        :param data: Source mapping to extract from.
        :type data: dict
        :param reserved: Set of keys to exclude from the result.
        :type reserved: set[str]
        :return: Filtered mapping.
        :rtype: dict
        """
        return {k: v for k, v in data.items() if k not in reserved}

    # ------------------------------------------------------------------
    # Lifecycle

    @override
    def connect(self) -> Any:
        """Open a new PostgreSQL connection (no-op when already connected).

        :return: Active connection object.
        :rtype: Any
        :raises RuntimeError: If the connection attempt fails.
        """
        if not self._is_closed():
            return self._connection

        self._connection = psycopg.connect(
            self.connection_string, row_factory=dict_row
        )
        logger.info("Connected to PostgreSQL database")
        return self._connection

    @override
    def disconnect(self) -> None:
        """Close the PostgreSQL connection.

        This method is idempotent and safe to call when no connection exists.

        :return: None
        :rtype: None
        """
        if self._connection is None:
            return

        try:
            if not getattr(self._connection, "closed", False):
                self._connection.close()
        finally:
            self._connection = None
            logger.info("Disconnected from PostgreSQL database")

    def load(
        self,
        data_path: str,
        table_name: str,
        if_exists: str = "replace"
    ) -> str:
        """
        Load ddata into PostgreSQL and return schema metadata.
        """
        
        if not os.path.exists(data_path):
            raise FileNotFoundError(f"Data file not found: {data_path}")
        
        if data_path.endswith(".csv"):
            # Read CSV into a DataFrame
            df = pd.read_csv(data_path)
            
            # Normalize column names
            df.columns = [
                col.strip()
                .lower()
                .replace(" ", "_")
                .replace("-", "_")
                for col in df.columns
            ]
            
            # Create SQLAlchemy engine
            engine = create_engine(
                self.connection_string.replace(
                    "postgresql://",
                    "postgresql+psycopg://"
                )
            )
            
            # Load DataFrame into PostgreSQL
            df.to_sql(
                table_name,
                engine,
                if_exists=if_exists,
                index=False,
                chunksize=5000,
                method="multi"
            )

            return {
                table_name: self._generate_schema_metadata(table_name)
            }
            
        else:
            raise ValueError(f"Unsupported file format: {data_path}. Only CSV is supported.")
            
        
    # ------------------------------------------------------------------
    # Core CRUD

    @override
    def query(
        self,
        query: str,
        params: Optional[Dict[str, Any]] = None,
        fetch: Optional[bool] = None,
    ):
        """
        Execute a SQL query.

        Only fetch rows when the statement actually returns a result set.
        In particular, don't mistake SELECT statements inside a CTE for
        a SELECT as the final statement.
        """
        query_clean = query.strip().rstrip(";")

        if fetch is None:
            # Remove leading comments if present.
            normalized = re.sub(
                r"^(?:\s|/\*.*?\*/|--[^\n]*(?:\n|$))*",
                "",
                query_clean,
                flags=re.DOTALL,
            ).strip()

            # PostgreSQL CTEs can contain SELECTs while the final statement
            # is UPDATE/INSERT/DELETE.
            if normalized.upper().startswith("WITH"):
                # Determine the actual statement following the CTE.
                #
                # For the current service, the safest rule is:
                # if the SQL contains RETURNING, fetch; otherwise don't.
                wants_fetch = bool(
                    re.search(r"\bRETURNING\b", normalized, re.IGNORECASE)
                )

                # A plain WITH ... SELECT does return rows.
                if not wants_fetch:
                    wants_fetch = bool(
                        re.search(
                            r"\)\s*(SELECT)\b",
                            normalized,
                            re.IGNORECASE | re.DOTALL,
                        )
                    )
            else:
                first_keyword = normalized.split(None, 1)[0].upper() if normalized else ""
                wants_fetch = first_keyword in {"SELECT", "SHOW", "DESCRIBE", "EXPLAIN"}

                # INSERT/UPDATE/DELETE with RETURNING produce rows.
                if re.search(r"\bRETURNING\b", normalized, re.IGNORECASE):
                    wants_fetch = True
        else:
            wants_fetch = fetch

        return self.execute(
            sql.SQL(query),
            params=params,
            fetch=wants_fetch,
        )

    @staticmethod
    def _statement_returns_rows(query: str) -> bool:
        """Best-effort check for whether *query* yields a result set."""
        stripped = query.strip().lstrip("(")
        first_word = stripped.split(None, 1)[0].upper() if stripped else ""

        if first_word in {"SELECT", "WITH", "VALUES", "TABLE", "EXPLAIN", "SHOW"}:
            return True
        if first_word in {"INSERT", "UPDATE", "DELETE"}:
            return "RETURNING" in stripped.upper()
        return False

    @override
    def retrieve(
        self, 
        table: str, 
        filters: Optional[dict] = None
    ) -> Optional[dict]:
        try:
            if not filters:
                statement = sql.SQL("SELECT * FROM {table}").format(
                    table=sql.Identifier(table)
                )
                return self.execute(statement, fetch=True)

            conditions = sql.SQL(" AND ").join(
                sql.SQL("{col} = %({param})s").format(
                    col=sql.Identifier(col),
                    param=sql.SQL(col),  # plain, unquoted placeholder name
                )
                for col in filters
            )
            statement = sql.SQL("SELECT * FROM {table} WHERE {conditions}").format(
                table=sql.Identifier(table),
                conditions=conditions,
            )
            return self.execute(statement, params=filters, fetch=True)

        except Exception:
            logger.exception("Failed to retrieve rows from PostgreSQL")
            return None

    @override
    def insert(
        self, 
        data: dict
    ) -> Any:
        """
        Insert a row using either raw SQL or a structured dict.

        :param data: Either ``{"sql": "...", "params": {...}}`` or
            ``{"table": "name", "values": {...}}`` or a flat mapping.
        :type data: dict
        :return: When the statement contains a ``RETURNING`` clause (as in
            ``WorkspaceService._insert_row``), a ``list[dict]`` of the
            returned rows (e.g. ``[{"id": 42}]``) so callers can read back
            generated values such as a new primary key. Otherwise, ``True``
            on success or ``False`` on failure.
        :rtype: Any
        """
        connection = self._ensure_connection()
        try:
            if "sql" in data:
                sql_text = data["sql"]
                # Previously this always called execute() with fetch=False,
                # which meant a `RETURNING id` clause was executed but its
                # result row was never fetched — cursor.fetchall() was simply
                # never called, and this method unconditionally returned
                # True. Callers that expect the new row's id back (e.g.
                # WorkspaceService._insert_row does
                # `result[0].get("id")`) would then always treat the insert
                # as if no id had been returned, even though the row was
                # actually written. Detect RETURNING and fetch when present.
                wants_returning = "returning" in sql_text.lower()
                result = self.execute(
                    sql_text, params=data.get("params") or {}, fetch=wants_returning
                )
                return result.get("rows", []) if wants_returning else True

            table = data.get("table")
            if not table:
                raise ValueError("Insert data must include 'table' or 'sql'.")

            values = data.get("values") or self._extract_values(
                data, {"table", "values", "sql", "params"}
            )
            statement = self._build_insert_statement(table, values)
            self.execute(statement, params=values)
            return True

        except Exception:
            self._safe_rollback(connection)
            logger.exception("Failed to insert row into PostgreSQL")
            return False

    @override
    def update(
        self, 
        query: str, 
        data: dict
    ) -> bool:
        """
        Update rows using either raw SQL or a structured dict.

        :param query: WHERE clause (or ignored when ``data`` contains ``sql``).
        :type query: str
        :param data: Either ``{"sql": "...", "params": {...}}`` or a
            structured mapping containing ``table`` and ``values``.
        :type data: dict
        :return: ``True`` on success, ``False`` otherwise.
        :rtype: bool
        """
        connection = self._ensure_connection()
        try:
            if "sql" in data:
                self.execute(data["sql"], params=data.get("params") or {})
                return True

            table = data.get("table")
            if not table:
                raise ValueError("Update data must include 'table' or 'sql'.")

            values = data.get("values") or self._extract_values(
                data, {"table", "values", "sql", "params", "where"}
            )
            where_clause = data.get("where", query)
            statement = self._build_update_statement(table, values, where_clause)
            self.execute(statement, params=values)
            return True

        except Exception:
            self._safe_rollback(connection)
            logger.exception("Failed to update rows in PostgreSQL")
            return False

    @override
    def delete(
        self, 
        query: str
    ) -> bool:
        """
        Execute a ``DELETE`` statement given as a plain SQL string.

        :param query: Full ``DELETE FROM ... WHERE ...`` SQL statement.
        :type query: str
        :return: ``True`` on success, ``False`` otherwise.
        :rtype: bool
        """
        connection = self._ensure_connection()
        try:
            self.execute(sql.SQL(query))
            return True
        except Exception:
            self._safe_rollback(connection)
            logger.exception("Failed to delete rows from PostgreSQL")
            return False

    @override
    def execute_many(
        self, 
        statement: str, 
        params_seq: list[dict]
    ) -> bool:
        """
        Execute *statement* for each parameter mapping in *params_seq*.

        All executions share a single transaction; any failure triggers a
        full rollback.

        :param statement: Parameterised SQL string (``%(col)s`` placeholders).
        :type statement: str
        :param params_seq: List of parameter dictionaries, one per execution.
        :type params_seq: list[dict]
        :return: ``True`` if all executions succeeded, ``False`` otherwise.
        :rtype: bool
        """
        connection = self._ensure_connection()
        try:
            with connection.cursor() as cursor:
                for params in params_seq:
                    cursor.execute(sql.SQL(statement), params)
            connection.commit()
            return True
        except Exception:
            self._safe_rollback(connection)
            logger.exception("Failed to execute batch statement in PostgreSQL")
            return False
