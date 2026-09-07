from typing import List


# ——————————————————————————————————————————————————————————————
# Workspace Exceptions

class WorkspaceNotFoundError(Exception):
    """Raised when a project_id does not resolve to a workspace row."""


class SourceNotFoundError(Exception):
    """Raised when a source_id does not resolve to an input source on a workspace."""


class DuplicateColumnError(ValueError):
    """
    Raised when two or more input sources share a non-key column name, making
    a column-wise merge ambiguous. Carries the offending column names so
    callers can surface them verbatim to the user.
    """

    def __init__(self, columns: List[str]):
        self.columns = columns
        super().__init__(
            "Column name(s) "
            + ", ".join(f"'{c}'" for c in columns)
            + " appear in more than one data source. Rename the column in one "
              "of the sources (or remove the duplicate) before building models."
        )


class DuplicateColumnError(Exception):
    """
    Raised when two or more input sources share a non-target column name in
    a way that can't be resolved into an unambiguous merge (see
    ``TabularDataProcessorAgent.merge_sources``).
    """

    def __init__(self, columns: List[str]):
        self.columns = columns
        super().__init__(
            f"Columns {columns} appear in more than one input source and "
            "can't be merged unambiguously."
        )

# ——————————————————————————————————————————————————————————————
# Notebook Errors

class NotebookNotFoundError(Exception):
    """
    Raised when a notebook id doesn't exist.
    """

class NotebookNotReadyError(Exception):
    """
    Raised when a cell is requested before a data source is bound.
    """

class CellNotFoundError(Exception):
    """
    Raised when a cell id doesn't exist on the given notebook.
    """

class CellNotReadyError(Exception):
    """
    Raised when a cell is requested before it has finished running.
    """
    
class CellRunError(Exception):
    """
    Raised when a cell run fails.
    """
    
class LastDataSourceError(Exception):
    """
    Raised when a user attempts to remove the last data source from a notebook.
    """

class DataSourceNotFoundError(Exception):
    """
    Raised when a data source id doesn't exist on the given notebook.
    """