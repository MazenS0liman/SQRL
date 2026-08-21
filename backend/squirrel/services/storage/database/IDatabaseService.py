"""
Abstract base class defining the database service interface.
"""
# ——————————————————————————————————————————————————————————————
# Imports

# Standard Libraries
from abc import ABC, abstractmethod
from typing import Any, Optional

# ——————————————————————————————————————————————————————————————
# Database Service Interface
class IDatabaseService(ABC):
    """
    Abstract base class for database service implementations.

    Subclasses must implement all abstract methods for their specific database
    backend. Optional lifecycle methods (``connect`` / ``disconnect``) have
    no-op defaults so lightweight implementations can skip them.

    The class also supports the context-manager protocol so callers can write::

        with MyDatabaseService() as db:
            db.query("SELECT 1")
    """

    def __init__(self) -> None:
        pass

    # ------------------------------------------------------------------
    # Lifecycle

    def connect(self) -> Any:
        """Establish a connection to the database.

        Subclasses should override this to open a real connection.

        :return: Connection object, or ``None`` for no-op defaults.
        :rtype: Any
        """
        pass

    def disconnect(self) -> None:
        """Close the database connection.

        Subclasses should override this to cleanly release resources.

        :return: None
        :rtype: None
        """
        pass

    # ------------------------------------------------------------------
    # Context-manager support

    def __enter__(self) -> "IDatabaseService":
        self.connect()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.disconnect()

    # ------------------------------------------------------------------
    # Core CRUD

    @abstractmethod
    def query(self, query: str) -> Optional[dict]:
        """Execute a read query and return all matching rows.

        :param query: Raw SQL string to execute.
        :type query: str
        :return: Mapping like ``{"success": True, "rows": [...], "columns": [...], "rowcount": N}`` on success, or ``None`` on failure.
        :rtype: Optional[dict]
        """
        pass

    @abstractmethod
    def insert(self, data: dict) -> bool:
        """Insert one row into the database.

        :param data: Either ``{"sql": "<raw SQL>", "params": {...}}`` for a prepared statement, or ``{"table": "<name>", "values": {...}}`` (plus any extra flat key/value pairs) for structured insertion.
        :type data: dict
        :return: ``True`` on success, ``False`` on failure.
        :rtype: bool
        """
        pass

    @abstractmethod
    def update(self, query: str, data: dict) -> bool:
        """Update rows identified by *query* with the values in *data*.

        :param query: WHERE clause (with or without the ``WHERE`` keyword) used to narrow which rows are updated, or a full SQL string when ``data`` contains ``"sql"``.
        :type query: str
        :param data: Either ``{"sql": "<raw SQL>", "params": {...}}`` for a prepared statement, or ``{"table": "<name>", "values": {...}, "where": "…"}`` for structured updates.
        :type data: dict
        :return: ``True`` on success, ``False`` on failure.
        :rtype: bool
        """
        pass

    @abstractmethod
    def delete(self, query: str) -> bool:
        """Delete rows matching *query*.

        :param query: Full ``DELETE FROM … WHERE …`` SQL statement.
        :type query: str
        :return: ``True`` on success, ``False`` on failure.
        :rtype: bool
        """
        pass

    @abstractmethod
    def retrieve(self, table: str, filters: Optional[dict] = None) -> Optional[dict]:
        """Fetch rows from *table*, optionally filtered by *filters*.

        :param table: Name of the table to query.
        :type table: str
        :param filters: Column/value pairs applied as ``col = value`` AND conditions. Pass ``None`` or ``{}`` to return all rows.
        :type filters: Optional[dict]
        :return: Same shape as :meth:`query` on success, or ``None`` on failure.
        :rtype: Optional[dict]
        """
        pass

    @abstractmethod
    def execute_many(self, statement: str, params_seq: list[dict]) -> bool:
        """Execute *statement* once for each parameter mapping in *params_seq*.

        Useful for bulk inserts or batch updates within a single transaction.

        :param statement: Parameterised SQL string (``%(col)s`` placeholders).
        :type statement: str
        :param params_seq: List of parameter dictionaries, one per execution.
        :type params_seq: list[dict]
        :return: ``True`` if all executions succeeded, ``False`` otherwise.
        :rtype: bool
        """
        pass
