"""In-memory test doubles for service and task tests."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, List, Optional


class FakeDB:
    """Stand-in for :class:`PostgresService` that never touches the network."""

    def __init__(self, results: Optional[List[Any]] = None) -> None:
        self.results: List[Any] = list(results or [])
        self.queries: List[SimpleNamespace] = []
        self.executes: List[SimpleNamespace] = []

    def queue(self, *results: Any) -> "FakeDB":
        """Queue results returned by successive :meth:`query` calls."""
        self.results.extend(results)
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

    @property
    def last_query(self) -> SimpleNamespace:
        """The most recent :meth:`query` call."""
        return self.queries[-1]
