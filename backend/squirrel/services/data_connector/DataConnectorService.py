#!/usr/bin/python
"""
Data Connector Service
=======================

Single owner of everything related to a **data connection**: its metadata
(name, type, live status), its config (with secret fields encrypted at rest),
and live access to the underlying source — Postgres, Databricks SQL
(data warehouse) and Lakebase (managed Postgres / lake).

This module absorbs what used to live in ``DataWarehouseService``: read-only
SQL validation, bounded queries, table listing, and column description. All of
it now runs against a stored *connection* (``connector_id``) instead of
environment-level settings, so credentials, ownership and status live in one
place.

Table
-----

.. code-block:: sql

    CREATE TABLE IF NOT EXISTS data_connections (
        connector_id     TEXT            PRIMARY KEY,
        owner_user_id    TEXT,
        name             TEXT            NOT NULL,
        type             TEXT            NOT NULL,
        status           TEXT            NOT NULL DEFAULT 'untested',
        config           JSONB           NOT NULL DEFAULT '{}'::jsonb,
        error            TEXT,
        last_tested_at   TIMESTAMPTZ,
        created_at       TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
        updated_at       TIMESTAMPTZ     NOT NULL DEFAULT NOW()
    );

Secret fields are stored encrypted and always redacted by
:meth:`DataConnectorService.to_public_dict`.

.. note::
    Encryption uses ``cryptography.fernet`` keyed by
    ``SQUIRREL_CONNECTOR_SECRET_KEY``. Without it secrets are only base64
    obfuscated — fine for a prototype, not for production.

.. note::
    Postgres/Lakebase go through :mod:`psycopg` (v3). Databricks goes through
    ``databricks-sql-connector`` (imported lazily).
"""
# ——————————————————————————————————————————————————————————————
# Imports

# Standard Libraries
import base64
import json
import re
import uuid
from datetime import date, datetime, time, timezone
from decimal import Decimal
from functools import partial
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# Third-Party Libraries
import pandas as pd

# Config
from squirrel.core.config import settings

# Services
from squirrel.services.storage.database.PostgresService import PostgresService

# Exceptions
from squirrel.schemas.connector import (
    ConnectorNotFoundError,
    ConnectorValidationError,
    ConnectorInUseError,
)

# Constants
from squirrel.constants.connector import (
    ConnectionStatus,
    ConnectorType,
    get_connector_type_spec,
)

# Logging
from loguru import logger

try:
    from cryptography.fernet import Fernet, InvalidToken
    _HAS_FERNET = True
except ImportError:
    _HAS_FERNET = False

try:
    import psycopg  # pyright: ignore[reportMissingImports]
    _HAS_PSYCOPG = True
except ImportError:
    _HAS_PSYCOPG = False

try:
    import pymysql
    _HAS_PYMYSQL = True
except ImportError:
    _HAS_PYMYSQL = False

# Connector type ids. Plain-string fallbacks keep this importable even if the
# constants module hasn't grown the attribute yet.
_POSTGRES = ConnectorType.POSTGRES
_MYSQL = ConnectorType.MYSQL
_DATABRICKS = getattr(ConnectorType, "DATABRICKS", "databricks")
_LAKEBASE = getattr(ConnectorType, "LAKEBASE", "lakebase")

# Types that speak SQL through the shared bounded/read-only query path.
_QUERYABLE_WAREHOUSE_TYPES = (_DATABRICKS, _LAKEBASE)

MAX_ROWS = 100_000

# ——————————————————————————————————————————————————————————————
# Secret handling

def _get_cipher() -> Optional["Fernet"]:
    if not _HAS_FERNET:
        return None
    key = settings.SQUIRREL_CONNECTOR_SECRET_KEY
    if not key:
        logger.warning(
            "SQUIRREL_CONNECTOR_SECRET_KEY is not set — connector secrets will "
            "be obfuscated, not encrypted. Set this env var in any real "
            "deployment."
        )
        return None
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception:
        logger.exception("SQUIRREL_CONNECTOR_SECRET_KEY is not a valid Fernet key.")
        return None


def _encrypt_secret(value: str) -> str:
    cipher = _get_cipher()
    if cipher is not None:
        return "fernet:" + cipher.encrypt(value.encode()).decode()
    return "b64:" + base64.b64encode(value.encode()).decode()


def _decrypt_secret(token: str) -> str:
    if token.startswith("fernet:"):
        cipher = _get_cipher()
        if cipher is None:
            raise RuntimeError(
                "Stored secret was encrypted with Fernet, but no usable key is "
                "configured to decrypt it now."
            )
        try:
            return cipher.decrypt(token[len("fernet:"):].encode()).decode()
        except InvalidToken as exc:
            raise RuntimeError("Could not decrypt stored secret (invalid token).") from exc
    if token.startswith("b64:"):
        return base64.b64decode(token[len("b64:"):].encode()).decode()
    return token


# ——————————————————————————————————————————————————————————————
# SQL safety (moved from DataWarehouseService)

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*(\.[A-Za-z_][A-Za-z0-9_$-]*){0,3}$")
_READ_START = re.compile(r"^(select|with)\b", re.IGNORECASE)
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|merge|drop|alter|create|truncate|grant|revoke|"
    r"copy|call|exec|execute|put|get|set|use|vacuum|optimize)\b",
    re.IGNORECASE,
)


def _strip_literals_and_comments(sql_text: str) -> str:
    """Remove string literals and comments so keyword checks can't be fooled."""
    sql_text = re.sub(r"/\*.*?\*/", " ", sql_text, flags=re.DOTALL)
    sql_text = re.sub(r"--[^\n]*", " ", sql_text)
    sql_text = re.sub(r"'(?:''|\\.|[^'\\])*'", "''", sql_text)
    sql_text = re.sub(r'"(?:""|[^"])*"', '""', sql_text)
    sql_text = re.sub(r"`[^`]*`", "``", sql_text)
    return sql_text


def validate_read_query(query: str) -> str:
    """Allow a single SELECT / WITH statement only. Returns the cleaned query."""
    if not query or not query.strip():
        raise ValueError("Query is empty.")
    cleaned = query.strip().rstrip(";").strip()
    skeleton = _strip_literals_and_comments(cleaned)
    if not _READ_START.match(skeleton.strip()):
        raise ValueError("Only SELECT or WITH queries are allowed.")
    if ";" in skeleton:
        raise ValueError("Multiple SQL statements are not allowed.")
    if _FORBIDDEN.search(skeleton):
        raise ValueError("Query contains a non read-only keyword.")
    return cleaned


def validate_identifier(value: str, label: str = "identifier") -> str:
    if not _IDENTIFIER.fullmatch(value or ""):
        raise ValueError(f"{label} must be a qualified SQL identifier.")
    return value


def _clamp_limit(limit: int) -> int:
    limit = int(limit)  # also blocks injection through the limit argument
    if limit < 1:
        raise ValueError("limit must be >= 1.")
    return min(limit, MAX_ROWS)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    return value


# ——————————————————————————————————————————————————————————————
# Postgres family (Postgres + Lakebase) — psycopg v3

def _pg_connect(config: Dict[str, str], sslmode: Optional[str] = None, timeout: int = 5):
    if not _HAS_PSYCOPG:
        raise RuntimeError(
            "psycopg is not installed; cannot reach Postgres-compatible "
            "connections. Install `psycopg[binary]>=3.2.0,<4.0.0`."
        )
    kwargs: Dict[str, Any] = dict(
        host=config["host"],
        port=int(config.get("port") or 5432),
        dbname=config["database"],
        user=config["username"],
        password=config.get("password", ""),
        connect_timeout=timeout,
    )
    if sslmode:
        kwargs["sslmode"] = sslmode
    return psycopg.connect(**kwargs)


def _test_pg(config: Dict[str, str], sslmode: Optional[str] = None, timeout: int = 5) -> None:
    _pg_connect(config, sslmode, timeout).close()


def _list_tables_pg(
    config: Dict[str, str], sslmode: Optional[str] = None, timeout: int = 5
) -> List[str]:
    conn = _pg_connect(config, sslmode, timeout)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_schema, table_name FROM information_schema.tables "
                "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') "
                "ORDER BY table_schema, table_name"
            )
            rows = cur.fetchall()
        # "public" is left implicit to keep the common case readable.
        return [name if schema == "public" else f"{schema}.{name}" for schema, name in rows]
    finally:
        conn.close()


def _run_pg(
    config: Dict[str, str],
    query: str,
    limit: int,
    sslmode: Optional[str] = None,
    timeout: int = 5,
) -> Tuple[List[str], List[Sequence[Any]]]:
    conn = _pg_connect(config, sslmode, timeout)
    try:
        conn.read_only = True  # enforced by the server, on top of validate_read_query
        with conn.cursor() as cur:
            cur.execute(query)
            rows = cur.fetchmany(limit)
            columns = [d.name for d in cur.description or ()]
        return columns, rows
    finally:
        conn.close()


def _quote_ident_pg(name: str) -> str:
    # "schema.table" or bare "table"
    return ".".join('"' + p.replace('"', '""') + '"' for p in name.split(".", 1))


# Lakebase = managed Postgres: same driver, TLS required, slower cold start.
_LAKEBASE_KW = dict(sslmode="require", timeout=30)


# ——————————————————————————————————————————————————————————————
# Databricks SQL (data warehouse)

def _dbx_connect(config: Dict[str, str]):
    try:
        from databricks import sql
    except ImportError as exc:
        raise RuntimeError("Install databricks-sql-connector.") from exc
    return sql.connect(
        server_hostname=config["server_hostname"],
        http_path=config["http_path"],
        access_token=config["token"],
    )


def _test_databricks(config: Dict[str, str]) -> None:
    conn = _dbx_connect(config)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchall()
    finally:
        conn.close()


def _run_databricks(
    config: Dict[str, str], query: str, limit: int
) -> Tuple[List[str], List[Sequence[Any]]]:
    conn = _dbx_connect(config)
    try:
        with conn.cursor() as cur:
            cur.execute(query)
            rows = cur.fetchmany(limit)
            columns = [c[0] for c in cur.description or ()]
        return columns, [tuple(r) for r in rows]
    finally:
        conn.close()


def _databricks_scope(config: Dict[str, str]) -> Tuple[Optional[str], Optional[str]]:
    """Optional ``catalog`` / ``schema`` narrowing from the connection config."""
    catalog = (config.get("catalog") or "").strip() or None
    schema = (config.get("schema") or "").strip() or None
    if catalog:
        validate_identifier(catalog, "catalog")
    if schema:
        validate_identifier(schema, "schema")
    return catalog, schema


_DATABRICKS_SKIP_CATALOGS = {"system", "__databricks_internal"}


def _list_tables_databricks(config: Dict[str, str]) -> List[str]:
    """
    Every table the token can see, across **all** catalogs and schemas, as
    fully-qualified ``catalog.schema.table`` names.

    Strategy:
      1. One query on ``system.information_schema.tables`` (Unity Catalog) —
         covers every catalog in a single round trip.
      2. If that isn't readable, ``SHOW CATALOGS`` then each catalog's own
         ``information_schema`` (a catalog that can't be read, e.g.
         ``hive_metastore``, is skipped rather than failing the listing;
         ``hive_metastore`` is then covered via ``SHOW SCHEMAS/TABLES``).

    The optional ``catalog`` / ``schema`` config fields now act as *filters*
    that narrow the list, never as the only thing listed.
    """
    want_catalog, want_schema = _databricks_scope(config)
    conn = _dbx_connect(config)
    found: List[Tuple[str, str, str]] = []

    def run(q: str) -> List[Sequence[Any]]:
        with conn.cursor() as cur:
            cur.execute(q)
            return [tuple(r) for r in cur.fetchall()]

    try:
        try:
            q = (
                "SELECT table_catalog, table_schema, table_name "
                "FROM system.information_schema.tables "
                "WHERE table_schema <> 'information_schema'"
            )
            found = [(r[0], r[1], r[2]) for r in run(q)]
        except Exception as exc:
            logger.info("system.information_schema unavailable ({}); listing per catalog.", exc)
            found = []
            for (catalog,) in [(r[0],) for r in run("SHOW CATALOGS")]:
                if catalog in _DATABRICKS_SKIP_CATALOGS:
                    continue
                if want_catalog and catalog != want_catalog:
                    continue
                try:
                    rows = run(
                        "SELECT table_schema, table_name FROM "
                        f"{_quote_ident_databricks(catalog)}.information_schema.tables "
                        "WHERE table_schema <> 'information_schema'"
                    )
                    found += [(catalog, r[0], r[1]) for r in rows]
                except Exception:
                    # No information_schema (e.g. hive_metastore): walk SHOW SCHEMAS/TABLES.
                    try:
                        qc = _quote_ident_databricks(catalog)
                        for (schema,) in [(r[0],) for r in run(f"SHOW SCHEMAS IN {qc}")]:
                            if schema == "information_schema":
                                continue
                            qs = f"{qc}.{_quote_ident_databricks(schema)}"
                            for r in run(f"SHOW TABLES IN {qs}"):
                                found.append((catalog, schema, r[1]))  # (database, tableName, ...)
                    except Exception as exc2:
                        logger.info("Skipping catalog {}: {}", catalog, exc2)
    finally:
        conn.close()

    out = sorted(
        {
            f"{c}.{s}.{t}"
            for c, s, t in found
            if c not in _DATABRICKS_SKIP_CATALOGS
            and (not want_catalog or c == want_catalog)
            and (not want_schema or s == want_schema)
        }
    )
    return out


def _quote_ident_databricks(name: str) -> str:
    return ".".join("`" + p.replace("`", "``") + "`" for p in name.split("."))


# ——————————————————————————————————————————————————————————————
# DataConnectorService


class DataConnectorService:
    """
    CRUD, secret handling, live testing **and** read-only data access for data
    connections (Postgres, MySQL, Databricks, Lakebase).

    Per-type behaviour is registered in the dispatch tables below; adding a
    source type means adding one entry to each table it supports.
    """

    _TABLE = "data_connections"

    _TABLE_LIST_DISPATCH: Dict[str, Callable[[Dict[str, str]], List[str]]] = {
        _POSTGRES: _list_tables_pg,
        _LAKEBASE: partial(_list_tables_pg, **_LAKEBASE_KW),
        _DATABRICKS: _list_tables_databricks,
    }

    _QUOTE_DISPATCH: Dict[str, Callable[[str], str]] = {
        _POSTGRES: _quote_ident_pg,
        _LAKEBASE: _quote_ident_pg,
        _DATABRICKS: _quote_ident_databricks,
    }

    _TEST_DISPATCH: Dict[str, Callable[[Dict[str, str]], None]] = {
        _POSTGRES: _test_pg,
        _LAKEBASE: partial(_test_pg, **_LAKEBASE_KW),
        _DATABRICKS: _test_databricks,
    }

    # (config, query, limit) -> (columns, rows)
    _QUERY_DISPATCH: Dict[
        str, Callable[[Dict[str, str], str, int], Tuple[List[str], List[Sequence[Any]]]]
    ] = {
        _POSTGRES: _run_pg,
        _LAKEBASE: partial(_run_pg, **_LAKEBASE_KW),
        _DATABRICKS: _run_databricks,
    }

    def __init__(self, db: Optional[PostgresService] = None) -> None:
        self._db = db or PostgresService()
        self._ensure_table()

    # ------------------------------------------------------------------
    # Schema / migration (idempotent — safe to run on every start)

    def _ensure_table(self) -> None:
        try:
            self._db.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self._TABLE} (
                    connector_id     TEXT            PRIMARY KEY,
                    owner_user_id    TEXT,
                    name             TEXT            NOT NULL,
                    type             TEXT            NOT NULL,
                    status           TEXT            NOT NULL DEFAULT 'untested',
                    config           JSONB           NOT NULL DEFAULT '{{}}'::jsonb,
                    error            TEXT,
                    last_tested_at   TIMESTAMPTZ,
                    created_at       TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
                    updated_at       TIMESTAMPTZ     NOT NULL DEFAULT NOW()
                );
                """,
                params={},
                fetch=False,
            )
            # Databases created before ownership scoping.
            self._db.execute(
                f"ALTER TABLE {self._TABLE} ADD COLUMN IF NOT EXISTS owner_user_id TEXT",
                params={},
                fetch=False,
            )
            # list_connections / get_connection always filter by owner.
            self._db.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{self._TABLE}_owner_created "
                f"ON {self._TABLE} (owner_user_id, created_at DESC)",
                params={},
                fetch=False,
            )
            self._db.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{self._TABLE}_type "
                f"ON {self._TABLE} (type)",
                params={},
                fetch=False,
            )
            logger.info("data_connections table ready.")
        except Exception:
            logger.exception("Failed to ensure data_connections table.")

    # ------------------------------------------------------------------
    # Validation

    @staticmethod
    def _validate_config(
        connector_type: str,
        config: Dict[str, str],
        existing_config: Optional[Dict[str, str]] = None,
    ) -> None:
        spec = get_connector_type_spec(connector_type)
        known_keys = set(spec.field_keys())
        unknown = set(config.keys()) - known_keys
        if unknown:
            raise ConnectorValidationError(
                f"Unknown field(s) for connector type '{connector_type}': {sorted(unknown)}."
            )

        missing = [
            f.key for f in spec.fields
            if f.required and not (config.get(f.key) or "").strip()
        ]
        if missing:
            raise ConnectorValidationError(
                f"Missing required field(s) for connector type '{connector_type}': {missing}."
            )

    # ------------------------------------------------------------------
    # CRUD

    def create_connection(
        self,
        name: str,
        connector_type: str,
        config: Dict[str, str],
        owner_user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if connector_type not in ConnectorType.ALL:
            raise ValueError(f"Unknown connector type '{connector_type}'.")

        self._validate_config(connector_type, config)
        stored_config = self._encrypt_config(connector_type, config)

        connector_id = f"conn_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc)

        result = self._db.insert(
            {
                "sql": f"""
                    INSERT INTO {self._TABLE} (
                        connector_id, owner_user_id, name, type, status, config, created_at, updated_at
                    ) VALUES (
                        %(connector_id)s, %(owner_user_id)s, %(name)s, %(type)s, %(status)s, %(config)s::jsonb, %(now)s, %(now)s
                    )
                    RETURNING connector_id, owner_user_id, name, type, status, config, error,
                              last_tested_at, created_at, updated_at
                """,
                "params": {
                    "connector_id": connector_id,
                    "owner_user_id": owner_user_id,
                    "name": name,
                    "type": connector_type,
                    "status": ConnectionStatus.UNTESTED,
                    "config": json.dumps(stored_config),
                    "now": now,
                },
            }
        )

        if not result or not isinstance(result, list) or not result[0].get("connector_id"):
            raise RuntimeError("Failed to create connection row.")

        logger.info("Created connection '{}' (connector_id={}, type={})", name, connector_id, connector_type)
        return result[0]

    def list_connections(
        self,
        limit: int = 200,
        owner_user_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        filters: Dict[str, Any] = {}
        if owner_user_id:
            filters["owner_user_id"] = owner_user_id
        result = self._db.retrieve(self._TABLE, filters=filters)
        rows = result.get("rows", []) if result else []
        rows.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return rows[:limit]

    def get_connection(
        self,
        connector_id: str,
        owner_user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        filters: Dict[str, Any] = {"connector_id": connector_id}
        if owner_user_id:
            filters["owner_user_id"] = owner_user_id
        result = self._db.retrieve(self._TABLE, filters=filters)
        rows = result.get("rows", []) if result else []
        if not rows:
            raise ConnectorNotFoundError(f"No connection with connector_id={connector_id}")
        return rows[0]

    def update_connection(
        self,
        connector_id: str,
        name: Optional[str] = None,
        config: Optional[Dict[str, str]] = None,
        owner_user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        row = self.get_connection(connector_id, owner_user_id=owner_user_id)
        fields: Dict[str, Any] = {}

        if name is not None:
            name = name.strip()
            if not name:
                raise ValueError("Connection name cannot be empty.")
            fields["name"] = name

        if config is not None:
            connector_type = row["type"]
            spec = get_connector_type_spec(connector_type)
            existing_config: Dict[str, str] = row.get("config") or {}

            merged = dict(config)
            for f in spec.fields:
                if f.secret and not (merged.get(f.key) or "").strip() and f.key in existing_config:
                    merged[f.key] = "__KEEP_EXISTING__"

            to_validate = {
                k: ("placeholder" if v == "__KEEP_EXISTING__" else v)
                for k, v in merged.items()
            }
            self._validate_config(connector_type, to_validate)

            final_config: Dict[str, str] = {}
            for key, value in merged.items():
                field_spec = next((f for f in spec.fields if f.key == key), None)
                if value == "__KEEP_EXISTING__":
                    final_config[key] = existing_config[key]
                elif field_spec is not None and field_spec.secret:
                    final_config[key] = _encrypt_secret(value)
                else:
                    final_config[key] = value

            fields["config"] = final_config
            fields["status"] = ConnectionStatus.UNTESTED
            fields["error"] = None

        if not fields:
            return row

        self._update_connection_row(connector_id, fields)
        return self.get_connection(connector_id, owner_user_id=owner_user_id)

    def _get_connector_usage(self, connector_id: str) -> Dict[str, List[str]]:
        """Notebooks / workspaces still referencing *connector_id* (best-effort)."""
        notebooks: List[str] = []
        workspaces: List[str] = []

        try:
            result = self._db.query(
                """
                SELECT DISTINCT n.name
                FROM sqrl_notebook_data_sources d
                JOIN sqrl_notebooks n ON n.id = d.notebook_id
                WHERE d.connector_id = %(connector_id)s
                """,
                params={"connector_id": connector_id},
            )
            notebooks = [row["name"] for row in (result or {}).get("rows", [])]
        except Exception:
            logger.debug(
                "Skipping notebook-usage check for connector {} (table not "
                "available yet).", connector_id,
            )

        try:
            result = self._db.query(
                """
                SELECT w.name
                FROM workspaces w
                WHERE EXISTS (
                    SELECT 1 FROM jsonb_array_elements(w.input_sources) AS src
                    WHERE src->>'connector_id' = %(connector_id)s
                )
                """,
                params={"connector_id": connector_id},
            )
            workspaces = [row["name"] for row in (result or {}).get("rows", [])]
        except Exception:
            logger.debug(
                "Skipping workspace-usage check for connector {} (table not "
                "available yet).", connector_id,
            )

        return {"notebooks": notebooks, "workspaces": workspaces}

    def delete_connection(
        self,
        connector_id: str,
        owner_user_id: Optional[str] = None,
    ) -> None:
        self.get_connection(connector_id, owner_user_id=owner_user_id)

        usage = self._get_connector_usage(connector_id)
        if usage["notebooks"] or usage["workspaces"]:
            parts = []
            if usage["notebooks"]:
                parts.append(f"notebook(s) [{', '.join(usage['notebooks'])}]")
            if usage["workspaces"]:
                parts.append(f"workspace(s) [{', '.join(usage['workspaces'])}]")
            raise ConnectorInUseError(
                f"Can't remove this connection — it's still used by "
                f"{' and '.join(parts)}. Remove it from those first."
            )

        try:
            self._db.execute(
                f"DELETE FROM {self._TABLE} WHERE connector_id = %(connector_id)s",
                params={"connector_id": connector_id},
                fetch=False,
            )
            logger.info("Deleted connection connector_id={}", connector_id)
        except Exception:
            logger.exception("Failed to delete connection connector_id={}", connector_id)
            raise

    def _update_connection_row(self, connector_id: str, fields: Dict[str, Any]) -> None:
        jsonb_cols = {"config"}
        set_parts = []
        params: Dict[str, Any] = {"connector_id": connector_id, "updated_at": datetime.now(timezone.utc)}
        for k, v in fields.items():
            if k in jsonb_cols:
                set_parts.append(f"{k} = %({k})s::jsonb")
                params[k] = json.dumps(v)
            else:
                set_parts.append(f"{k} = %({k})s")
                params[k] = v
        set_parts.append("updated_at = %(updated_at)s")
        set_clause = ", ".join(set_parts)
        try:
            self._db.execute(
                f"UPDATE {self._TABLE} SET {set_clause} WHERE connector_id = %(connector_id)s",
                params=params,
                fetch=False,
            )
        except Exception:
            logger.exception("Failed to update connection connector_id={}", connector_id)
            raise

    # ------------------------------------------------------------------
    # Introspection

    def _resolve(
        self, connector_id: str, owner_user_id: Optional[str] = None
    ) -> Tuple[str, Dict[str, str]]:
        """``(connector_type, decrypted_config)`` for a stored connection."""
        row = self.get_connection(connector_id, owner_user_id=owner_user_id)
        return row["type"], self._decrypt_config(row.get("config") or {})

    def list_tables(
        self,
        connector_id: str,
        owner_user_id: Optional[str] = None,
    ) -> List[str]:
        """
        Queryable tables for this connection, to populate a table picker.
        Raises ``NotImplementedError`` for types with no lister registered.
        """
        connector_type, config = self._resolve(connector_id, owner_user_id)
        lister = self._TABLE_LIST_DISPATCH.get(connector_type)
        if lister is None:
            raise NotImplementedError(
                f"Listing tables for connector type '{connector_type}' isn't "
                "wired up yet."
            )
        return lister(config)

    def describe_table(
        self,
        connector_id: str,
        table_name: str,
        owner_user_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Column names and types for a table (Postgres, Lakebase, Databricks)."""
        connector_type, config = self._resolve(connector_id, owner_user_id)
        runner = self._QUERY_DISPATCH.get(connector_type)
        if runner is None:
            raise NotImplementedError(
                f"Describing tables for connector type '{connector_type}' isn't wired up yet."
            )

        validate_identifier(table_name, "table")
        parts = table_name.split(".")
        name = parts[-1]
        schema = parts[-2] if len(parts) > 1 else None

        if connector_type == _DATABRICKS:
            catalog = parts[-3] if len(parts) >= 3 else _databricks_scope(config)[0]
            info = f"{catalog}.information_schema" if catalog else "information_schema"
        else:
            schema = schema or "public"
            info = "information_schema"

        where = f"table_name = '{name}'"
        if schema:
            where += f" AND table_schema = '{schema}'"
        query = (
            f"SELECT column_name, data_type FROM {info}.columns "
            f"WHERE {where} ORDER BY ordinal_position"
        )
        columns, rows = self._run(runner, config, query, MAX_ROWS)
        return [dict(zip(columns, r)) for r in rows]

    def build_table_query(
        self,
        connector_id: str,
        table_name: str,
        columns: Optional[List[str]] = None,
        owner_user_id: Optional[str] = None,
    ) -> str:
        """
        Turn a table name (and optional column subset) into a safe SELECT.
        Table and column names are validated against what the connector
        reports before being quoted.
        """
        connector_type, _ = self._resolve(connector_id, owner_user_id)

        known_tables = set(self.list_tables(connector_id, owner_user_id=owner_user_id))
        if table_name not in known_tables:
            raise ValueError(
                f"'{table_name}' is not a known table for connector '{connector_id}'."
            )

        quoter = self._QUOTE_DISPATCH.get(connector_type)
        if quoter is None:
            raise NotImplementedError(
                f"Building a table query for connector type '{connector_type}' "
                "isn't wired up yet."
            )

        if not columns:
            return f"SELECT * FROM {quoter(table_name)}"

        known_columns = set(
            self._known_columns(connector_id, table_name, owner_user_id=owner_user_id)
        )
        unknown_columns = [c for c in columns if c not in known_columns]
        if unknown_columns:
            raise ValueError(
                f"Unknown column(s) for table '{table_name}': {unknown_columns}."
            )

        column_sql = ", ".join(quoter(c) for c in columns)
        return f"SELECT {column_sql} FROM {quoter(table_name)}"

    def _known_columns(
        self,
        connector_id: str,
        table_name: str,
        owner_user_id: Optional[str] = None,
    ) -> List[str]:
        full_query = self.build_table_query(
            connector_id, table_name, owner_user_id=owner_user_id
        )
        df = self.preview_dataframe(
            connector_id, full_query, limit=1, owner_user_id=owner_user_id
        )
        return list(df.columns)

    def preview_table(
        self,
        connector_id: str,
        table_name: str,
        limit: int = 5,
        columns: Optional[List[str]] = None,
        owner_user_id: Optional[str] = None,
    ) -> pd.DataFrame:
        query = self.build_table_query(
            connector_id, table_name, columns, owner_user_id=owner_user_id
        )
        return self.preview_dataframe(
            connector_id, query, limit, owner_user_id=owner_user_id
        )

    def preview_dataframe(
        self,
        connector_id: str,
        query: str,
        limit: int = 50,
        owner_user_id: Optional[str] = None,
    ) -> pd.DataFrame:
        """Run *query* capped to *limit* rows. Service-built queries only."""
        wrapped = f"SELECT * FROM ({query}) AS _squirrel_preview LIMIT {int(limit)}"
        return self.fetch_dataframe(connector_id, wrapped, owner_user_id=owner_user_id)

    # ------------------------------------------------------------------
    # Testing

    def test_connection(
        self, connector_id: str, owner_user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Live connectivity check; persists status. A failed *test* is a normal
        outcome (status='error'), not an exception.
        """
        row = self.get_connection(connector_id, owner_user_id=owner_user_id)
        connector_type = row["type"]
        tester = self._TEST_DISPATCH.get(connector_type)

        now = datetime.now(timezone.utc)

        if tester is None:
            fields = {
                "status": ConnectionStatus.ERROR,
                "error": f"No tester registered for connector type '{connector_type}'.",
                "last_tested_at": now,
            }
            self._update_connection_row(connector_id, fields)
            return self.get_connection(connector_id, owner_user_id=owner_user_id)

        try:
            decrypted_config = self._decrypt_config(row.get("config") or {})
            tester(decrypted_config)
        except NotImplementedError as exc:
            fields = {"status": ConnectionStatus.ERROR, "error": str(exc), "last_tested_at": now}
        except Exception as exc:
            logger.info("Connection test failed for connector_id={}: {}", connector_id, exc)
            fields = {"status": ConnectionStatus.ERROR, "error": str(exc), "last_tested_at": now}
        else:
            fields = {"status": ConnectionStatus.CONNECTED, "error": None, "last_tested_at": now}

        self._update_connection_row(connector_id, fields)
        return self.get_connection(connector_id, owner_user_id=owner_user_id)

    # ------------------------------------------------------------------
    # Data retrieval

    @staticmethod
    def _run(
        runner: Callable[[Dict[str, str], str, int], Tuple[List[str], List[Sequence[Any]]]],
        config: Dict[str, str],
        query: str,
        limit: int,
    ) -> Tuple[List[str], List[Sequence[Any]]]:
        """
        Execute via a driver, turning driver-level failures (bad table, bad
        SQL, network) into ``RuntimeError`` so the API answers 400 with the
        driver's message instead of an unhandled 500.
        """
        try:
            return runner(config, query, limit)
        except (ValueError, NotImplementedError):
            raise
        except Exception as exc:
            logger.info("Query failed: {}", exc)
            raise RuntimeError(f"Query failed: {exc}") from exc

    def fetch_query(
        self,
        connector_id: str,
        query: str,
        limit: int = 100,
        owner_user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Bounded, read-only query against any queryable connection
        (Postgres, Lakebase, Databricks). Returns JSON-safe rows.
        """
        connector_type, config = self._resolve(connector_id, owner_user_id)
        runner = self._QUERY_DISPATCH.get(connector_type)
        if runner is None:
            raise NotImplementedError(
                f"Querying connector type '{connector_type}' is not supported."
            )

        safe_query = validate_read_query(query)
        limit = _clamp_limit(limit)
        bounded = f"SELECT * FROM ({safe_query}) AS source_data LIMIT {limit}"
        columns, rows = self._run(runner, config, bounded, limit)
        records = [{c: _jsonable(v) for c, v in zip(columns, row)} for row in rows]
        return {"columns": columns, "rows": records, "row_count": len(records)}

    def fetch_dataframe(
        self,
        connector_id: str,
        query: Optional[str] = None,
        owner_user_id: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        Resolve a connection into an in-memory DataFrame (extension point for
        ``WorkspaceService.fetch_data``). ``query`` is required — there is no
        default table. Databricks/Lakebase reads are capped at ``MAX_ROWS``.
        """
        connector_type, config = self._resolve(connector_id, owner_user_id)

        if connector_type in (_DATABRICKS, _LAKEBASE) or connector_type == _POSTGRES:
            if not query:
                raise NotImplementedError(
                    f"Reading a {connector_type} connector requires a 'query' — "
                    "there's no default table to select from."
                )

        if connector_type == _POSTGRES:
            if not _HAS_PSYCOPG:
                raise NotImplementedError(
                    "psycopg is not installed; cannot read from Postgres connectors. "
                    "Install `psycopg[binary]>=3.2.0,<4.0.0`."
                )
            from sqlalchemy import create_engine
            from sqlalchemy.engine import URL

            conn_url = URL.create(
                drivername="postgresql+psycopg",
                username=config["username"],
                password=config.get("password", ""),
                host=config["host"],
                port=int(config.get("port") or 5432),
                database=config["database"],
            )
            engine = create_engine(conn_url)
            try:
                return pd.read_sql_query(query, engine)
            finally:
                engine.dispose()

        if connector_type in (_DATABRICKS, _LAKEBASE):
            columns, rows = self._run(
                self._QUERY_DISPATCH[connector_type], config, query, MAX_ROWS
            )
            return pd.DataFrame.from_records([tuple(r) for r in rows], columns=columns)

        if connector_type == _MYSQL:
            if not _HAS_PYMYSQL:
                raise NotImplementedError("pymysql is not installed; cannot read from MySQL connectors.")
            if not query:
                raise NotImplementedError(
                    "Reading a MySQL connector requires a 'query' — there's no "
                    "default table to select from."
                )
            conn = pymysql.connect(
                host=config["host"],
                port=int(config.get("port") or 3306),
                db=config["database"],
                user=config["username"],
                password=config.get("password", ""),
                connect_timeout=5,
            )
            try:
                return pd.read_sql_query(query, conn)
            finally:
                conn.close()

        raise NotImplementedError(
            f"Reading data from connector type '{connector_type}' isn't wired up "
            "yet — add a branch here once the corresponding client library is "
            "integrated."
        )

    # ------------------------------------------------------------------
    # Secret handling helpers

    @staticmethod
    def _encrypt_config(connector_type: str, config: Dict[str, str]) -> Dict[str, str]:
        spec = get_connector_type_spec(connector_type)
        secret_keys = set(spec.secret_keys())
        return {
            k: (_encrypt_secret(v) if k in secret_keys and v else v)
            for k, v in config.items()
        }

    @staticmethod
    def _decrypt_config(config: Dict[str, str]) -> Dict[str, str]:
        decrypted: Dict[str, str] = {}
        for k, v in (config or {}).items():
            if isinstance(v, str) and (v.startswith("fernet:") or v.startswith("b64:")):
                decrypted[k] = _decrypt_secret(v)
            else:
                decrypted[k] = v
        return decrypted

    # ------------------------------------------------------------------
    # Public-facing serialization

    @staticmethod
    def to_public_dict(row: Dict[str, Any]) -> Dict[str, Any]:
        """Row safe for the API layer: every secret field redacted to ``""``."""
        connector_type = row.get("type")
        try:
            spec = get_connector_type_spec(connector_type) if connector_type else None
        except ValueError:
            spec = None
        secret_keys = set(spec.secret_keys()) if spec else set()

        raw_config = row.get("config") or {}
        public_config = {
            k: ("" if k in secret_keys else v)
            for k, v in raw_config.items()
        }

        return {**row, "config": public_config}

    def get_postgres_service(
        self, connector_id: str, owner_user_id: Optional[str] = None
    ) -> PostgresService:
        """
        A connected :class:`PostgresService` for a Postgres-compatible
        connection (Postgres, or Lakebase over TLS).
        """
        connector_type, config = self._resolve(connector_id, owner_user_id)

        if connector_type not in (_POSTGRES, _LAKEBASE):
            raise NotImplementedError(
                f"get_postgres_service() only supports Postgres-compatible connectors; "
                f"connector '{connector_id}' is type '{connector_type}'."
            )

        connection_string = (
            f"postgresql://{config['username']}:{config.get('password', '')}"
            f"@{config['host']}:{int(config.get('port') or 5432)}/{config['database']}"
        )
        if connector_type == _LAKEBASE:
            connection_string += "?sslmode=require"
        return PostgresService(connection_string=connection_string)