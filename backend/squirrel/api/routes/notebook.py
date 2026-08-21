#!/usr/bin/python
"""
Notebook Routes
===============

Overview
--------

HTTP surface for the Notebook feature:

1. **CRUD** — create / list / get / delete notebooks.
2. **Data source binding** — a notebook owns one or more data sources, either
   an uploaded CSV (freshly uploaded, or reused from an existing Files-page
   upload) or a table on an existing connector managed by
   :class:`DataConnectorService`.
3. **Cell execution** — every cell (a full EDA sweep, a dashboard, a specific
   question, or a markdown note) is answered by
   :class:`TabularDataExploratoryAgent`.

There is deliberately no data-manipulation/"transform" endpoint: a user asking
for something that reshapes the data (dedupe, filter outliers, fill missing
values, ...) is served by the normal ``POST .../cells`` question pipeline,
which — via
:meth:`TabularDataExploratoryAgent.classify_question_intent`/``process_view_request``
— can answer with a saved, non-destructive SQL VIEW instead of a chart when
that fits the request better. The underlying data source's live table is
never rewritten by this service.

Integration seam
-----------------

Connector-backed notebooks need a live, query-capable :class:`PostgresService`
pointed at the connector's underlying database. This service calls
``DataConnectorService.get_postgres_service(connector_id)`` for that — if
your :class:`DataConnectorService` doesn't yet expose this, add a small
method that decrypts the stored config and returns a connected
``PostgresService`` (the same object ``preview_table`` presumably builds
internally). Everything else in this file is fully wired.

"""
# ——————————————————————————————————————————————————————————————
# Imports

# Standard Libraries
import re
import os
import tempfile
from pathlib import Path
from typing import List, Optional

# FastAPI
from fastapi import APIRouter, Body, Depends, File as FastAPIFile, HTTPException, Response, UploadFile, status
from fastapi.concurrency import run_in_threadpool

# Auth
from squirrel.api.routes.auth import get_current_user
from squirrel.schemas.auth import AuthUserRead

# Services
from squirrel.services.notebook.NotebookService import NotebookService

# Schema
from squirrel.schemas.notebook import (
    Notebook,
    NotebookCell,
    DashboardLayout,
    DashboardVersion,
    DataSourcePreview,
    CreateNotebookRequest,
    RenameNotebookRequest,
    AddConnectorDataSourceRequest,
    AddExistingFileDataSourceRequest,
    RenameDataSourceRequest,
    RunCellRequest,
    UpdateCellRequest,
    SaveDashboardLayoutRequest,
)

# Errors
from squirrel.schemas.error import (
    CellNotFoundError,
    NotebookNotFoundError,
    NotebookNotReadyError,
    LastDataSourceError,
    DataSourceNotFoundError
)

# Logging
from loguru import logger

router = APIRouter()

# ——————————————————————————————————————————————————————————————
# Helpers

def _svc() -> NotebookService:
    return NotebookService()


def _not_found(notebook_id: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Notebook '{notebook_id}' not found.",
    )


def _source_not_found(source_id: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Data source '{source_id}' not found.",
    )


def _cell_not_found(cell_id: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Cell '{cell_id}' not found.",
    )


def _slugify(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", name or "").strip("-").lower()

# ——————————————————————————————————————————————————————————————
# Notebook CRUD

@router.get("", summary="List all notebooks for the current user", response_model=list[Notebook])
async def list_notebooks(
    current_user: AuthUserRead = Depends(get_current_user),
) -> list[Notebook]:
    return await run_in_threadpool(_svc().list_notebooks, owner_user_id=current_user.id)


@router.post(
    "",
    summary="Create a new notebook",
    status_code=status.HTTP_201_CREATED,
    response_model=Notebook,
)
async def create_notebook(
    body: CreateNotebookRequest = Body(...),
    current_user: AuthUserRead = Depends(get_current_user),
) -> Notebook:
    return await run_in_threadpool(
        _svc().create_notebook,
        name=body.name,
        description=body.description,
        owner_user_id=current_user.id,
    )


@router.get("/{notebook_id}", summary="Get a notebook by id", response_model=Notebook)
async def get_notebook(
    notebook_id: str,
    current_user: AuthUserRead = Depends(get_current_user),
) -> Notebook:
    svc = _svc()
    try:
        return await run_in_threadpool(svc.get_notebook, notebook_id, owner_user_id=current_user.id)
    except NotebookNotFoundError:
        raise _not_found(notebook_id)


@router.patch("/{notebook_id}", summary="Rename/update a notebook", response_model=Notebook)
async def rename_notebook(
    notebook_id: str,
    body: RenameNotebookRequest = Body(...),
    current_user: AuthUserRead = Depends(get_current_user),
) -> Notebook:
    svc = _svc()
    try:
        return await run_in_threadpool(
            svc.rename_notebook,
            notebook_id,
            name=body.name,
            description=body.description,
            owner_user_id=current_user.id,
        )
    except NotebookNotFoundError:
        raise _not_found(notebook_id)


@router.delete(
    "/{notebook_id}",
    summary="Delete a notebook",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_notebook(
    notebook_id: str,
    current_user: AuthUserRead = Depends(get_current_user),
) -> None:
    svc = _svc()
    try:
        await run_in_threadpool(svc.delete_notebook, notebook_id, owner_user_id=current_user.id)
    except NotebookNotFoundError:
        raise _not_found(notebook_id)


@router.post(
    "/{notebook_id}/duplicate",
    summary="Duplicate a notebook",
    status_code=status.HTTP_201_CREATED,
    response_model=Notebook,
)
async def duplicate_notebook(
    notebook_id: str,
    current_user: AuthUserRead = Depends(get_current_user),
) -> Notebook:
    svc = _svc()
    try:
        return await run_in_threadpool(svc.duplicate_notebook, notebook_id, owner_user_id=current_user.id)
    except NotebookNotFoundError:
        raise _not_found(notebook_id)


# ——————————————————————————————————————————————————————————————
# Data sources

@router.post(
    "/{notebook_id}/data-source/upload",
    summary="Upload a CSV and bind it to this notebook",
    response_model=Notebook,
)
async def add_csv_data_source(
    notebook_id: str,
    file: UploadFile = FastAPIFile(...),
    current_user: AuthUserRead = Depends(get_current_user),
) -> Notebook:
    svc = _svc()
    try:
        await run_in_threadpool(svc.get_notebook, notebook_id, owner_user_id=current_user.id)
    except NotebookNotFoundError:
        raise _not_found(notebook_id)

    original_filename = file.filename or "upload.csv"
    suffix = Path(original_filename).suffix or ".csv"

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp_path = tmp.name
            while chunk := await file.read(1024 * 1024):
                tmp.write(chunk)
    finally:
        await file.close()

    try:
        return await run_in_threadpool(
            svc.add_csv_data_source,
            notebook_id=notebook_id,
            local_path=tmp_path,
            original_filename=original_filename,
            owner_user_id=current_user.id,
        )
    except NotebookNotFoundError:
        raise _not_found(notebook_id)
    except Exception as exc:
        logger.exception("Failed to bind CSV upload to notebook {}", notebook_id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


@router.post(
    "/{notebook_id}/data-source/existing-file",
    summary="Bind an already-uploaded file (from the Files page) to this notebook",
    response_model=Notebook,
)
async def add_existing_file_data_source(
    notebook_id: str,
    body: AddExistingFileDataSourceRequest = Body(...),
    current_user: AuthUserRead = Depends(get_current_user),
) -> Notebook:
    svc = _svc()
    try:
        return await run_in_threadpool(
            svc.add_existing_file_data_source,
            notebook_id=notebook_id,
            file_url=body.file_url,
            file_name=body.file_name,
            owner_user_id=current_user.id,
        )
    except NotebookNotFoundError:
        raise _not_found(notebook_id)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        logger.exception("Failed to bind existing file to notebook {}", notebook_id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.post(
    "/{notebook_id}/data-source/connector",
    summary="Bind a connector table to this notebook",
    response_model=Notebook,
)
async def add_connector_data_source(
    notebook_id: str,
    body: AddConnectorDataSourceRequest = Body(...),
    current_user: AuthUserRead = Depends(get_current_user),
) -> Notebook:
    svc = _svc()
    try:
        return await run_in_threadpool(
            svc.add_connector_data_source,
            notebook_id=notebook_id,
            connector_id=body.connector_id,
            table_name=body.table_name,
            owner_user_id=current_user.id,
        )
    except NotebookNotFoundError:
        raise _not_found(notebook_id)
    except Exception as exc:
        logger.exception("Failed to bind connector table to notebook {}", notebook_id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.get(
    "/{notebook_id}/data-source/preview",
    summary="Preview rows from one of the notebook's data sources",
    response_model=DataSourcePreview,
)
async def preview_data_source(
    notebook_id: str,
    source_id: Optional[str] = None,
    limit: int = 50,
    current_user: AuthUserRead = Depends(get_current_user),
) -> DataSourcePreview:
    svc = _svc()
    try:
        return await run_in_threadpool(
            svc.preview_data_source,
            notebook_id=notebook_id,
            data_source_id=source_id,
            limit=limit,
            owner_user_id=current_user.id,
        )
    except NotebookNotFoundError:
        raise _not_found(notebook_id)
    except (DataSourceNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except NotebookNotReadyError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.patch(
    "/{notebook_id}/data-source/{source_id}",
    summary="Rename a data source (syncs to Files page for upload-backed sources)",
    response_model=Notebook,
)
async def rename_data_source(
    notebook_id: str,
    source_id: str,
    body: RenameDataSourceRequest = Body(...),
    current_user: AuthUserRead = Depends(get_current_user),
) -> Notebook:
    svc = _svc()
    try:
        return await run_in_threadpool(
            svc.rename_data_source,
            notebook_id=notebook_id,
            source_id=source_id,
            label=body.label,
            owner_user_id=current_user.id,
        )
    except NotebookNotFoundError:
        raise _not_found(notebook_id)
    except DataSourceNotFoundError:
        raise _source_not_found(source_id)


@router.delete(
    "/{notebook_id}/data-source/{source_id}",
    summary="Detach a data source from this notebook",
    response_model=Notebook,
)
async def remove_data_source(
    notebook_id: str,
    source_id: str,
    current_user: AuthUserRead = Depends(get_current_user),
) -> Notebook:
    svc = _svc()
    try:
        return await run_in_threadpool(
            svc.remove_data_source,
            notebook_id=notebook_id,
            source_id=source_id,
            owner_user_id=current_user.id,
        )
    except NotebookNotFoundError:
        raise _not_found(notebook_id)
    except DataSourceNotFoundError:
        raise _source_not_found(source_id)
    except LastDataSourceError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


# ——————————————————————————————————————————————————————————————
# Cells

@router.get(
    "/{notebook_id}/cells",
    summary="List all cells for a notebook",
    response_model=list[NotebookCell],
)
async def list_cells(
    notebook_id: str,
    current_user: AuthUserRead = Depends(get_current_user),
) -> list[NotebookCell]:
    svc = _svc()
    try:
        return await run_in_threadpool(svc.list_cells, notebook_id, owner_user_id=current_user.id)
    except NotebookNotFoundError:
        raise _not_found(notebook_id)


@router.post(
    "/{notebook_id}/cells",
    summary="Run a new cell (EDA sweep, dashboard, or question)",
    status_code=status.HTTP_201_CREATED,
    response_model=NotebookCell,
)
async def run_cell(
    notebook_id: str,
    body: RunCellRequest = Body(...),
    current_user: AuthUserRead = Depends(get_current_user),
) -> NotebookCell:
    svc = _svc()
    try:
        return await run_in_threadpool(
            svc.run_cell,
            notebook_id=notebook_id,
            cell_type=body.type,
            query=body.query,
            data_source_ids=body.data_source_ids,
            owner_user_id=current_user.id,
        )
    except NotebookNotFoundError:
        raise _not_found(notebook_id)
    except NotebookNotReadyError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        logger.exception("Cell run failed notebook={}", notebook_id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.put(
    "/{notebook_id}/cells/{cell_id}",
    summary="Re-run an existing cell in place (regenerate)",
    response_model=NotebookCell,
)
async def update_cell(
    notebook_id: str,
    cell_id: str,
    body: UpdateCellRequest = Body(...),
    current_user: AuthUserRead = Depends(get_current_user),
) -> NotebookCell:
    svc = _svc()
    try:
        return await run_in_threadpool(
            svc.update_cell,
            notebook_id=notebook_id,
            cell_id=cell_id,
            query=body.query,
            datasource_ids=body.data_source_ids,
            owner_user_id=current_user.id,
        )
    except NotebookNotFoundError:
        raise _not_found(notebook_id)
    except CellNotFoundError:
        raise _cell_not_found(cell_id)
    except NotebookNotReadyError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        logger.exception("Cell update failed notebook={} cell={}", notebook_id, cell_id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.delete(
    "/{notebook_id}/cells/{cell_id}",
    summary="Delete a cell",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_cell(
    notebook_id: str,
    cell_id: str,
    current_user: AuthUserRead = Depends(get_current_user),
) -> None:
    svc = _svc()
    try:
        await run_in_threadpool(svc.delete_cell, notebook_id, cell_id, owner_user_id=current_user.id)
    except NotebookNotFoundError:
        raise _not_found(notebook_id)
    except CellNotFoundError:
        raise _cell_not_found(cell_id)


@router.patch(
    "/{notebook_id}/cells/{cell_id}/dashboard/layout",
    summary="Save dashboard customizations (order, edits, custom charts, notes)",
    response_model=NotebookCell,
)
async def save_dashboard_layout(
    notebook_id: str,
    cell_id: str,
    body: SaveDashboardLayoutRequest = Body(...),
    current_user: AuthUserRead = Depends(get_current_user),
) -> NotebookCell:
    svc = _svc()
    layout = DashboardLayout(
        order=body.order,
        hidden_ids=body.hidden_ids,
        hints=body.hints,
        overrides=body.overrides,
        custom_charts=body.custom_charts,
        text_blocks=body.text_blocks,
    )
    try:
        return await run_in_threadpool(
            svc.save_dashboard_layout,
            notebook_id,
            cell_id,
            layout,
            owner_user_id=current_user.id,
        )
    except NotebookNotFoundError:
        raise _not_found(notebook_id)
    except CellNotFoundError:
        raise _cell_not_found(cell_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get(
    "/{notebook_id}/cells/{cell_id}/dashboard/versions",
    summary="List archived dashboard versions (previous regenerations)",
    response_model=list[DashboardVersion],
)
async def list_dashboard_versions(
    notebook_id: str,
    cell_id: str,
    current_user: AuthUserRead = Depends(get_current_user),
) -> list[DashboardVersion]:
    svc = _svc()
    try:
        return await run_in_threadpool(
            svc.list_dashboard_versions,
            notebook_id,
            cell_id,
            owner_user_id=current_user.id,
        )
    except NotebookNotFoundError:
        raise _not_found(notebook_id)
    except CellNotFoundError:
        raise _cell_not_found(cell_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get(
    "/{notebook_id}/cells/{cell_id}/dashboard/versions/{version_id}",
    summary="Get one archived dashboard version",
    response_model=DashboardVersion,
)
async def get_dashboard_version(
    notebook_id: str,
    cell_id: str,
    version_id: str,
    current_user: AuthUserRead = Depends(get_current_user),
) -> DashboardVersion:
    svc = _svc()
    try:
        return await run_in_threadpool(
            svc.get_dashboard_version,
            notebook_id,
            cell_id,
            version_id,
            owner_user_id=current_user.id,
        )
    except NotebookNotFoundError:
        raise _not_found(notebook_id)
    except CellNotFoundError:
        raise _cell_not_found(cell_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get(
    "/{notebook_id}/export/ipynb",
    summary="Export this notebook as a Jupyter notebook (.ipynb)",
)
async def export_notebook_ipynb(
    notebook_id: str,
    current_user: AuthUserRead = Depends(get_current_user),
) -> Response:
    svc = _svc()
    try:
        notebook = await run_in_threadpool(svc.get_notebook, notebook_id, owner_user_id=current_user.id)
        ipynb_bytes = await run_in_threadpool(
            svc.export_notebook_ipynb, notebook_id, owner_user_id=current_user.id
        )
    except NotebookNotFoundError:
        raise _not_found(notebook_id)
    except Exception as exc:
        logger.exception("Failed to export notebook {} as ipynb", notebook_id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))

    filename = f"{_slugify(notebook.name) or notebook_id}.ipynb"
    return Response(
        content=ipynb_bytes,
        media_type="application/x-ipynb+json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

@router.patch("/{notebook_id}/cells/reorder", summary="Reorder cells in a notebook", response_model=list[NotebookCell])
async def reorder_cells(
    notebook_id: str,
    body: List[str] = Body(..., description="Ordered list of cell ids"),
    current_user: AuthUserRead = Depends(get_current_user),
) -> list[NotebookCell]:
    svc = _svc()
    try:
        return await run_in_threadpool(
            svc.reorder_cells,
            notebook_id=notebook_id,
            ordered_cell_ids=body,
            owner_user_id=current_user.id,
        )
    except NotebookNotFoundError:
        raise _not_found(notebook_id)
    except CellNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        logger.exception("Failed to reorder cells for notebook {}", notebook_id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))