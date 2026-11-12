Data Schemas
============

Reference documentation for SQRL data schemas.

Api
---

.. autoclass:: squirrel.schemas.api.ConversationMessage
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.api.LLMTokens
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.api.AgentRequest
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.api.AgentResponse
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.api.ModelMetric
   :members:
   :show-inheritance:

Argument Spec
-------------

.. autoclass:: squirrel.schemas.argument_spec.ArgumentSpec
   :members:
   :show-inheritance:

Auth
----

.. autoclass:: squirrel.schemas.auth.RegisterRequest
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.auth.LoginRequest
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.auth.AuthUserRead
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.auth.AuthTokenResponse
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.auth.CurrentUserResponse
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.auth.RequestLogContext
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.auth.LogEntry
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.auth.RecentLogsResponse
   :members:
   :show-inheritance:

Connector
---------

.. autoclass:: squirrel.schemas.connector.ConnectorField
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.connector.ConnectorType
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.connector.TableSelection
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.connector.TablePreview
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.connector.ConnectorCreateRequest
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.connector.ConnectorUpdateRequest
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.connector.ConnectorPreviewRequest
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.connector.ConnectorSourcesRequest
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.connector.ConnectorPreviewResponse
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.connector.ConnectorTablesPreviewResponse
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.connector.DataConnectionOut
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.connector.ConnectorNotFoundError
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.connector.ConnectorValidationError
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.connector.ConnectorInUseError
   :members:
   :show-inheritance:

Eda
---

.. autoclass:: squirrel.schemas.eda.AxisSpec
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.eda.Hypothesis
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.eda.SqlSpec
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.eda.ChartResult
   :members:
   :show-inheritance:

Error
-----

.. autoclass:: squirrel.schemas.error.WorkspaceNotFoundError
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.error.SourceNotFoundError
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.error.DuplicateColumnError
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.error.NotebookNotFoundError
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.error.NotebookNotReadyError
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.error.CellNotFoundError
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.error.CellNotReadyError
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.error.CellRunError
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.error.LastDataSourceError
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.error.DataSourceNotFoundError
   :members:
   :show-inheritance:

File
----

.. autoclass:: squirrel.schemas.file.FileType
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.file.File
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.file.UploadedSourceOut
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.file.FileRequest
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.file.RenameFileRequest
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.file.FileResponse
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.file.FilesResponse
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.file.FileProcessingIssue
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.file.FileRecordNotFound
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.file.FileInUseError
   :members:
   :show-inheritance:

Notebook
--------

.. autoclass:: squirrel.schemas.notebook.NotebookDataSource
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.notebook.DataSourcePreview
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.notebook.NotebookChart
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.notebook.DashboardLayout
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.notebook.DashboardVersion
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.notebook.NotebookCell
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.notebook.Notebook
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.notebook.CreateNotebookRequest
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.notebook.RenameNotebookRequest
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.notebook.AddConnectorDataSourceRequest
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.notebook.RunCellRequest
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.notebook.UpdateCellRequest
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.notebook.AddExistingFileDataSourceRequest
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.notebook.RenameDataSourceRequest
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.notebook.SaveDashboardLayoutRequest
   :members:
   :show-inheritance:

Token
-----

.. autoclass:: squirrel.schemas.token.TokenCreateRequest
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.token.TokenRead
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.token.TokenListResponse
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.token.TokenCreateResponse
   :members:
   :show-inheritance:

Workspace
---------

.. autoclass:: squirrel.schemas.workspace.DataSource
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.workspace.SourceColumnsRequest
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.workspace.WorkspaceRecord
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.workspace.WorkspaceRunRecord
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.workspace.WorkspaceCreateRequest
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.workspace.WorkspaceUpdateRequest
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.workspace.UploadResponse
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.workspace.BuildRequest
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.workspace.ModelMetric
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.workspace.ModelFileOut
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.workspace.BuildResponse
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.workspace.ModelsResponse
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.workspace.PreprocessedDataResponse
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.workspace.PreprocessRequest
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.workspace.PreprocessResponse
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.workspace.ModelBuildRequest
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.workspace.ModelBuildResponse
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.workspace.WorkspaceListResponse
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.workspace.PredictRequest
   :members:
   :show-inheritance:

.. autoclass:: squirrel.schemas.workspace.PredictResponse
   :members:
   :show-inheritance:
