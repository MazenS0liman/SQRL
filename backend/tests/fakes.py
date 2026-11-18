"""In-memory test doubles for service and task tests."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, List, Optional


class FakeDB:
    """Stand-in for :class:`PostgresService` that never touches the network."""

    def __init__(
        self,
        results: Optional[List[Any]] = None,
        insert_results: Optional[List[Any]] = None,
        retrieve_results: Optional[List[Any]] = None,
    ) -> None:
        self.results: List[Any] = list(results or [])
        self.insert_results: List[Any] = list(insert_results or [])
        self.retrieve_results: List[Any] = list(retrieve_results or [])
        self.load_results: List[Any] = []
        self.queries: List[SimpleNamespace] = []
        self.executes: List[SimpleNamespace] = []
        self.inserts: List[SimpleNamespace] = []
        self.retrieves: List[SimpleNamespace] = []
        self.loads: List[SimpleNamespace] = []
        self.disconnects = 0

    def queue(self, *results: Any) -> "FakeDB":
        """Queue results returned by successive :meth:`query` calls."""
        self.results.extend(results)
        return self

    def queue_insert(self, *results: Any) -> "FakeDB":
        """Queue results returned by successive :meth:`insert` calls."""
        self.insert_results.extend(results)
        return self

    def queue_retrieve(self, *results: Any) -> "FakeDB":
        """Queue results returned by successive :meth:`retrieve` calls."""
        self.retrieve_results.extend(results)
        return self

    def queue_load(self, *results: Any) -> "FakeDB":
        """Queue schema metadata returned by successive :meth:`load` calls."""
        self.load_results.extend(results)
        return self

    def execute(self, sql: str, params: Optional[dict] = None, fetch: bool = False):
        self.executes.append(
            SimpleNamespace(sql=sql, params=params or {}, fetch=fetch)
        )
        return {"rowcount": 0}

    def query(self, sql: str, params: Optional[dict] = None, fetch: Optional[bool] = None):
        self.queries.append(
            SimpleNamespace(sql=sql, params=params or {}, fetch=fetch)
        )
        if self.results:
            return self.results.pop(0)
        return {"rows": []}

    def insert(self, statement: dict):
        """Record an insert statement and return the next queued row list."""
        self.inserts.append(
            SimpleNamespace(
                sql=statement.get("sql", ""),
                params=statement.get("params", {}) or {},
            )
        )
        if self.insert_results:
            return self.insert_results.pop(0)
        return []

    def retrieve(self, table: str, filters: Optional[dict] = None):
        """Record a table read and return the next queued result."""
        self.retrieves.append(SimpleNamespace(table=table, filters=filters or {}))
        if self.retrieve_results:
            return self.retrieve_results.pop(0)
        return {"rows": []}

    def load(self, data_path: str, table_name: str, if_exists: str = "replace"):
        """Record a CSV load and return the next queued schema metadata."""
        self.loads.append(
            SimpleNamespace(
                data_path=data_path, table_name=table_name, if_exists=if_exists
            )
        )
        if self.load_results:
            return self.load_results.pop(0)
        return {"row_count": 0, "columns": []}

    def disconnect(self) -> None:
        """Match :meth:`PostgresService.disconnect` so ``finally`` blocks work."""
        self.disconnects += 1

    @property
    def last_query(self) -> SimpleNamespace:
        """The most recent :meth:`query` call."""
        return self.queries[-1]
