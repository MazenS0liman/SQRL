#!/usr/bin/python
"""
Data Connector Service
=======================

Owns everything related to a **data connection**: its metadata (name,
type, live status) and its config, including encrypting/redacting whatever
fields the connector type marks as secret (see
``squirrel.constants.connector.CONNECTOR_TYPE_SPECS``).

Table
-----

.. code-block:: sql

    CREATE TABLE IF NOT EXISTS data_connections (
        connector_id     TEXT            PRIMARY KEY,
        name             TEXT            NOT NULL,
        type             TEXT            NOT NULL,
        status           TEXT            NOT NULL DEFAULT 'untested',
        config           JSONB           NOT NULL DEFAULT '{}'::jsonb,
        error            TEXT,
        last_tested_at   TIMESTAMPTZ,
        created_at       TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
        updated_at       TIMESTAMPTZ     NOT NULL DEFAULT NOW()
    );

Secret fields (e.g. passwords, service-account JSON) are stored in
``config`` **encrypted**, never in plaintext, and are always redacted
(returned as ``""``) by :meth:`to_public_dict` — the API layer should only
ever hand callers the output of that method, never a raw row.

.. note::
    Encryption uses ``cryptography.fernet`` when the package is available,
    keyed by the ``SQUIRREL_CONNECTOR_SECRET_KEY`` environment variable. If
    either is missing, secrets are still stored obfuscated (base64) rather
    than in plaintext, but this is **not** a substitute for a real secrets
    manager in production — swap ``_encrypt_secret``/``_decrypt_secret`` for
    calls to one (Vault, KMS, etc.) when this moves past a prototype.

.. note::
    Postgres connectivity (testing + reading) goes through :mod:`psycopg`
    (v3), matching :class:`PostgresService` elsewhere in this codebase —
    not the legacy :mod:`psycopg2`. Install it via
    ``psycopg[binary]>=3.2.0,<4.0.0``.
"""
# ——————————————————————————————————————————————————————————————
# Imports

# Standard Libraries
import base64
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

# Third-Party Libraries
import pandas as pd

# Services
from squirrel.services.storage.database.PostgresService import PostgresService

# Exceptiosn
from squirrel.schemas.connector import (
    ConnectorNotFoundError,
    ConnectorValidationError,
    ConnectorInUseError,
)

# Constants
from squirrel.constants.connector import (
    ConnectionStatus,
    ConnectorType,
    get_connector_type_spec
)

# Logging
from loguru import logger

# Optional: real symmetric encryption for secret config values. Falls back
# to a clearly-marked obfuscation (not real security) if unavailable, the
# same graceful-degradation pattern used for optional ML dependencies
# elsewhere in this codebase (see TabularDataModelBuilderAgent's xgboost/
# lightgbm imports).
try:
    from cryptography.fernet import Fernet, InvalidToken
    _HAS_FERNET = True
except ImportError:
    _HAS_FERNET = False

# Optional: live-connection testers per connector type. Each import failure
# only disables that one type's Test button (surfaced as a clear error),
# never the whole service.
#
# Postgres uses psycopg (v3) — same driver as PostgresService — not the
# legacy psycopg2. `psycopg[binary]` ships prebuilt wheels so there's no
# libpq/pg_config build-toolchain requirement, same rationale as the
# psycopg2-binary note this replaces.
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

# ——————————————————————————————————————————————————————————————
# Secret handling

def _get_cipher() -> Optional["Fernet"]:
    if not _HAS_FERNET:
        return None
    key = os.environ.get("SQUIRREL_CONNECTOR_SECRET_KEY")
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
    # No usable cipher — obfuscate so it's at least not plaintext-legible
    # in the database, and tag it so _decrypt_secret knows how to reverse it.
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
    # Unrecognised prefix — treat as legacy plaintext rather than crashing.
    return token


# ——————————————————————————————————————————————————————————————
# Live connection testers — one per ConnectorType. Add an entry to
# ``DataConnectorService._TEST_DISPATCH`` when adding a new tester.

def _test_postgres(config: Dict[str, str]) -> None:
    if not _HAS_PSYCOPG:
        raise RuntimeError(
            "psycopg is not installed; cannot test Postgres connections. "
            "Install `psycopg[binary]>=3.2.0,<4.0.0`."
        )
    conn = psycopg.connect(
        host=config["host"],
        port=int(config.get("port") or 5432),
        dbname=config["database"],
        user=config["username"],
        password=config.get("password", ""),
        connect_timeout=5,
    )
    conn.close()

# ——————————————————————————————————————————————————————————————
# List Tables

def _list_tables_postgres(
    config: Dict[str, str]
) -> List[str]:
    if not _HAS_PSYCOPG:
        raise RuntimeError(
            "psycopg is not installed; cannot list Postgres tables. "
            "Install `psycopg[binary]>=3.2.0,<4.0.0`."
        )
    conn = psycopg.connect(
        host=config["host"],
        port=int(config.get("port") or 5432),
        dbname=config["database"],
        user=config["username"],
        password=config.get("password", ""),
        connect_timeout=5,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_schema, table_name FROM information_schema.tables "
                "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') "
                "ORDER BY table_schema, table_name"
            )
            rows = cur.fetchall()
        # Qualify with schema only when it isn't the default 'public', to
        # keep the common case ("public.customers") readable as just
        # "customers" in the picker.
        return [name if schema == "public" else f"{schema}.{name}" for schema, name in rows]
    finally:
        conn.close()

def _quote_ident_postgres(table_name: str) -> str:
    # table_name may be "schema.table" (see _list_tables_postgres) or bare "table".
    parts = table_name.split(".", 1)
    return ".".join(f'"{p}"' for p in parts)

# ——————————————————————————————————————————————————————————————
# DataConnectorService


class DataConnectorService:
    """
    Coordinate CRUD, secret handling, and live testing for data
    connections used by workspaces as an alternative/additional input
    source to file uploads.

    **Usage:**

    1. Create / list / fetch / update / delete connections.
    2. Validate a connector type's required fields on create.
    3. Encrypt secret fields at rest; never surface them in plaintext.
    4. Run a live connectivity check ("Test") per connector type and
       persist the resulting status/error.
    5. Resolve a connection into an in-memory DataFrame for the workspace
       build pipeline (extension point — only a subset of types today).
    """

    _TABLE = "data_connections"

    _TABLE_LIST_DISPATCH: Dict[str, Callable[[Dict[str, str]], List[str]]] = {
        ConnectorType.POSTGRES: _list_tables_postgres
    }
    
    _QUOTE_DISPATCH: Dict[str, Callable[[str], str]] = {
        ConnectorType.POSTGRES: _quote_ident_postgres
    }
    
    _TEST_DISPATCH: Dict[str, Callable[[Dict[str, str]], None]] = {
        ConnectorType.POSTGRES: _test_postgres
    }

    def __init__(
        self, 
        db: Optional[PostgresService] = None
    ) -> None:
        self._db = db or PostgresService()
        
        # Ensure the data_connections table exists and has the expected columns.
        self._ensure_table()

    def _ensure_table(self) -> None:
        try:
            self._db.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self._TABLE} (
                    connector_id     TEXT            PRIMARY KEY,
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
        """
        Check that every required field for *connector_type* is present.

        On update (``existing_config`` given), a blank secret field is
        allowed — it means "keep the current value" and is filled in by
        :meth:`update_connection` before this is (re)validated against the
        merged config.
        """
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
    ) -> Dict[str, Any]:
        """
        Create a new connection. Secret fields in *config* are encrypted
        before being persisted.

        :raises ValueError: if ``connector_type`` isn't recognised.
        :raises ConnectorValidationError: if required fields are missing.
        """
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
                        connector_id, name, type, status, config, created_at, updated_at
                    ) VALUES (
                        %(connector_id)s, %(name)s, %(type)s, %(status)s, %(config)s::jsonb, %(now)s, %(now)s
                    )
                    RETURNING connector_id, name, type, status, config, error,
                              last_tested_at, created_at, updated_at
                """,
                "params": {
                    "connector_id": connector_id,
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
        limit: int = 200
    ) -> List[Dict[str, Any]]:
        """
        Return all connections, newest first.
        """
        result = self._db.retrieve(self._TABLE, filters={})
        rows = result.get("rows", []) if result else []
        rows.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return rows[:limit]

    def get_connection(
        self,
        connector_id: str
    ) -> Dict[str, Any]:
        """
        Fetch a single connection row, or raise ``ConnectorNotFoundError``.
        """
        result = self._db.retrieve(
            self._TABLE,
            filters={
                "connector_id": connector_id
                }
            )
        rows = result.get("rows", []) if result else []
        if not rows:
            raise ConnectorNotFoundError(f"No connection with connector_id={connector_id}")
        return rows[0]

    def update_connection(
        self,
        connector_id: str,
        name: Optional[str] = None,
        config: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        Update a connection's name and/or config.

        A blank value for a secret field in *config* means "keep the
        existing value" (matching the frontend form's behaviour) — it's
        merged in from the stored config before validation and encryption,
        rather than overwriting the secret with an empty string.
        """
        row = self.get_connection(connector_id)
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
                    # Blank secret on update => keep the existing encrypted value as-is.
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
            # Config changed — require a fresh Test before trusting status again.
            fields["status"] = ConnectionStatus.UNTESTED
            fields["error"] = None

        if not fields:
            return row

        self._update_connection_row(connector_id, fields)
        return self.get_connection(connector_id)

    def _get_connector_usage(self, connector_id: str) -> Dict[str, List[str]]:
        """
        Find every notebook and workspace still referencing *connector_id*,
        so :meth:`delete_connection` can refuse to remove a connection that's
        still wired into live analysis.

        Both lookups are best-effort against tables owned by other services
        (``sqrl_notebooks``/``sqrl_notebook_data_sources`` from
        :class:`NotebookService`, ``workspaces`` from :class:`WorkspaceService`)
        rather than importing those services directly, to avoid a circular
        service dependency. Either table may not exist yet if that feature has
        never been used — treated as "no usage there", not an error.

        :return: ``{"notebooks": [names...], "workspaces": [names...]}``.
        :rtype: Dict[str, List[str]]
        """
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
            # Most likely: sqrl_notebooks/sqrl_notebook_data_sources don't
            # exist yet because no notebook has ever been created.
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
        connector_id: str
    ) -> None:
        """
        Permanently delete a connection.

        :raises ConnectorNotFoundError: if the connector doesn't exist.
        :raises ConnectorInUseError: if any notebook or workspace still has
            a data/input source bound to this connector — those must be
            detached first (remove the data source from the notebook, or
            remove the source from the workspace) so a delete here can never
            silently orphan a live notebook cell or workspace run.
        """
        self.get_connection(connector_id)

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

    def _update_connection_row(
        self,
        connector_id: str,
        fields: Dict[str, Any]
    ) -> None:
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


    def list_tables(
        self, 
        connector_id: str
    ) -> List[str]:
        """
        Best-effort list of queryable tables for this connection, to
        populate a table picker. Raises ``NotImplementedError`` for
        connector types with no lister registered (Snowflake, BigQuery,
        Google Sheets today).
        """
        row = self.get_connection(connector_id)
        connector_type = row["type"]
        lister = self._TABLE_LIST_DISPATCH.get(connector_type)
        if lister is None:
            raise NotImplementedError(
                f"Listing tables for connector type '{connector_type}' isn't "
                "wired up yet."
            )
        config = self._decrypt_config(row.get("config") or {})
        return lister(config)


    def build_table_query(
        self,
        connector_id: str,
        table_name: str,
        columns: Optional[List[str]] = None,
    ) -> str:
        """
        Turn a table name — plus, optionally, a subset of its columns — into a
        safe SELECT query. Both the table name and every column name are
        validated against what this connector actually reports before being
        quoted and used, same guarantee as before: nothing here can be used to
        smuggle arbitrary SQL in via a name-shaped string.

        :param columns: Column names to select. ``None`` or empty means "every
            column" (``SELECT *``), same as today's behaviour.
        """
        row = self.get_connection(connector_id)
        connector_type = row["type"]

        known_tables = set(self.list_tables(connector_id))
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

        known_columns = set(self._known_columns(connector_id, table_name))
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
        table_name: str
    ) -> List[str]:
        """
        Cheap way to learn a table's column names for validating a column
        subset: an unrestricted, zero-row-cost preview. Reuses the existing
        preview machinery rather than adding a per-dialect information_schema
        query.
        """
        full_query = self.build_table_query(connector_id, table_name)  # columns=None -> SELECT *
        df = self.preview_dataframe(connector_id, full_query, limit=1)
        return list(df.columns)

    def preview_table(
        self,
        connector_id: str,
        table_name: str,
        limit: int = 5,
        columns: Optional[List[str]] = None,
    ) -> pd.DataFrame:
        """
        Preview the first *limit* rows of a specific table, restricted to
        *columns* if given.
        """
        query = self.build_table_query(connector_id, table_name, columns)
        return self.preview_dataframe(connector_id, query, limit)

    def preview_dataframe(
        self,
        connector_id: str,
        query: str,
        limit: int = 50,
    ) -> pd.DataFrame:
        """
        Internal helper: run *query* capped to *limit* rows via an outer
        LIMIT wrapper. Only called with queries this service built itself
        (:meth:`build_table_query`) — never with caller-supplied SQL.
        """
        wrapped = f"SELECT * FROM ({query}) AS _squirrel_preview LIMIT {int(limit)}"
        return self.fetch_dataframe(connector_id, wrapped)

    # ------------------------------------------------------------------
    # Testing

    def test_connection(self, connector_id: str) -> Dict[str, Any]:
        """
        Run a live connectivity check for a connection and persist the
        resulting status. Never raises for a failed *test* — a connection
        that can't be reached is a normal outcome (status='error'), not a
        service failure. Only genuinely unexpected errors bubble up.
        """
        row = self.get_connection(connector_id)
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
            return self.get_connection(connector_id)

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
        return self.get_connection(connector_id)

    # ------------------------------------------------------------------
    # Data retrieval — extension point for WorkspaceService.fetch_data

    def fetch_dataframe(
        self,
        connector_id: str,
        query: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        Resolve a connection into an in-memory DataFrame.

        This is the method ``WorkspaceService.fetch_data`` should call once
        a workspace build needs a connector source's live data. Only
        Postgres/MySQL are wired up (via the given ``query``); other types
        raise ``NotImplementedError`` with a clear message, same as the
        rest of this service's extension points.

        Postgres reads go through a short-lived SQLAlchemy engine on the
        ``postgresql+psycopg://`` dialect (psycopg v3) rather than a raw
        DBAPI connection — this matches how :meth:`PostgresService.load`
        already hands DataFrames to/from Postgres elsewhere in this
        codebase, and keeps ``pandas.read_sql_query`` on its
        fully-supported path instead of the raw-connection one pandas is
        deprecating.

        :param query: SQL query to run — required for both Postgres and
            MySQL since there's no single default table to select from.
        """
        row = self.get_connection(connector_id)
        connector_type = row["type"]
        config = self._decrypt_config(row.get("config") or {})

        if connector_type == ConnectorType.POSTGRES:
            if not _HAS_PSYCOPG:
                raise NotImplementedError(
                    "psycopg is not installed; cannot read from Postgres connectors. "
                    "Install `psycopg[binary]>=3.2.0,<4.0.0`."
                )
            if not query:
                raise NotImplementedError(
                    "Reading a Postgres connector requires a 'query' — there's no "
                    "default table to select from."
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

        if connector_type == ConnectorType.MYSQL:
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
    def _encrypt_config(
        connector_type: str,
        config: Dict[str, str]
    ) -> Dict[str, str]:
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
    def to_public_dict(
        row: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Convert a raw DB row into a dict safe to hand to the API layer:
        every secret field is redacted to ``""`` regardless of what's
        stored, and non-secret fields are passed through as-is (they were
        never encrypted).
        """
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

    def get_postgres_service(self, connector_id: str) -> PostgresService:
        """
        Resolve a Postgres-backed connection into a connected, query-capable
        :class:`PostgresService` pointed at the connector's own database.

        Used by callers (e.g. NotebookService) that need to run multiple
        queries against a connector-backed data source, rather than a single
        one-shot DataFrame read like :meth:`fetch_dataframe`.

        :raises ConnectorNotFoundError: if ``connector_id`` doesn't exist.
        :raises NotImplementedError: if this connector isn't Postgres-backed —
            only Postgres connectors can be resolved into a PostgresService
            today; other types (MySQL, Snowflake, BigQuery, Google Sheets)
            would need their own query-capable service class first.
        """
        row = self.get_connection(connector_id)
        connector_type = row["type"]

        if connector_type != ConnectorType.POSTGRES:
            raise NotImplementedError(
                f"get_postgres_service() only supports Postgres connectors; "
                f"connector '{connector_id}' is type '{connector_type}'."
            )

        config = self._decrypt_config(row.get("config") or {})
        connection_string = (
            f"postgresql://{config['username']}:{config.get('password', '')}"
            f"@{config['host']}:{int(config.get('port') or 5432)}/{config['database']}"
        )
        return PostgresService(connection_string=connection_string)
