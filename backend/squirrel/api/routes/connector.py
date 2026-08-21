#!/usr/bin/python
"""
Connector Route
================

Backs the frontend's Data Connectors page (``DataConnectorsPage.tsx``) and
its ``connectorsApi`` client:

1. ``GET /connectors/types``
   The catalog of supported connector types and their form fields — the
   "Add a source" tiles are built from this.

2. ``GET /connectors``
   List every configured connection, for the "Connected sources" list.

3. ``POST /connectors``
   Create a new connection (``connectorsApi.create``).

4. ``PATCH /connectors/{connector_id}``
   Rename a connection and/or update its config
   (``connectorsApi.update``) — a blank secret field means "keep the
   current value", matching the form's placeholder behaviour.

5. ``POST /connectors/{connector_id}/test``
   Run a live connectivity check and persist the resulting status
   (``connectorsApi.test``).

6. ``DELETE /connectors/{connector_id}``
   Remove a connection (``connectorsApi.remove``).

Every response uses :class:`DataConnectionOut`, which always redacts
secret fields — the frontend never receives a decrypted password/key back,
consistent with the "leave blank to keep current value" pattern already
built into the edit form.
"""
# ——————————————————————————————————————————————————————————————
# Imports

# Standard Libraries
from typing import List
import pandas as pd

# Third-Party Libraries
from fastapi import APIRouter, Body, HTTPException, status
from fastapi.concurrency import run_in_threadpool

# Services
from squirrel.services.data_connector.DataConnectorService import (
    DataConnectorService,
    ConnectorNotFoundError,
    ConnectorValidationError,
    ConnectorInUseError,
)

# Constants
from squirrel.constants.connector import CONNECTOR_TYPE_SPECS

# Schema
from squirrel.schemas.connector import (
    ConnectorType,
    ConnectorField,
    ConnectorCreateRequest,
    ConnectorUpdateRequest,
    DataConnectionOut,
    ConnectorTablesPreviewResponse,
    TablePreview
)

# Logging
from loguru import logger

# API Router
router = APIRouter()

# ——————————————————————————————————————————————————————————————
# Helpers

def _connector_service() -> DataConnectorService:
    return DataConnectorService()


def _not_found(connector_id: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Connection '{connector_id}' not found.",
    )


def _to_out(row: dict) -> DataConnectionOut:
    return DataConnectionOut(**DataConnectorService.to_public_dict(row))


# ——————————————————————————————————————————————————————————————
# Endpoints — connector type catalog


@router.get(
    "/types",
    summary="List supported connector types and their form fields",
    response_model=List[ConnectorType],
)
async def list_connector_types() -> List[ConnectorType]:
    """
    Backs the "Add a source" tiles: one entry per connector type, with the
    fields the create form should render.
    """
    return [
        ConnectorType(
            id=spec.id,
            label=spec.label,
            description=spec.description,
            fields=[
                ConnectorField(
                    key=f.key, label=f.label, type=f.type,
                    required=f.required, placeholder=f.placeholder,
                )
                for f in spec.fields
            ],
        )
        for spec in CONNECTOR_TYPE_SPECS.values()
    ]


# ——————————————————————————————————————————————————————————————
# Endpoints — connection CRUD


@router.get(
    "",
    summary="List all data connections",
    response_model=List[DataConnectionOut],
)
async def list_connections() -> List[DataConnectionOut]:
    rows = await run_in_threadpool(_connector_service().list_connections)
    return [_to_out(row) for row in rows]


@router.post(
    "",
    summary="Create a new data connection",
    status_code=status.HTTP_201_CREATED,
    response_model=DataConnectionOut,
)
async def create_connection(request: ConnectorCreateRequest = Body(...)) -> DataConnectionOut:
    connector_svc = _connector_service()
    try:
        row = await run_in_threadpool(
            connector_svc.create_connection, request.name, request.type, request.config
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except ConnectorValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception:
        logger.exception("Failed to create connection '{}'", request.name)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create connection.",
        )
    return _to_out(row)


@router.patch(
    "/{connector_id}",
    summary="Rename a connection and/or update its config",
    response_model=DataConnectionOut,
)
async def update_connection(
    connector_id: str,
    request: ConnectorUpdateRequest = Body(...),
) -> DataConnectionOut:
    connector_svc = _connector_service()
    try:
        row = await run_in_threadpool(
            connector_svc.update_connection, connector_id, request.name, request.config
        )
    except ConnectorNotFoundError:
        raise _not_found(connector_id)
    except (ValueError, ConnectorValidationError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return _to_out(row)


@router.delete(
    "/{connector_id}",
    summary="Delete a data connection",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_connection(connector_id: str) -> None:
    connector_svc = _connector_service()
    try:
        await run_in_threadpool(connector_svc.delete_connection, connector_id)
    except ConnectorNotFoundError:
        raise _not_found(connector_id)
    except ConnectorInUseError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except Exception:
        logger.exception("Failed to delete connection connector_id={}", connector_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete connection.",
        )


# ——————————————————————————————————————————————————————————————
# Endpoints — test


@router.post(
    "/{connector_id}/test",
    summary="Run a live connectivity check for a connection",
    response_model=DataConnectionOut,
)
async def test_connection(connector_id: str) -> DataConnectionOut:
    """
    Attempts to actually connect using the stored (decrypted) config and
    persists the resulting status ('connected' or 'error') — a failed test
    is a normal 200 response with ``status: "error"`` and a message, not an
    HTTP error, since "can't reach this database" is an expected outcome
    the user needs to see in the row, not a server failure.
    """
    connector_svc = _connector_service()
    try:
        row = await run_in_threadpool(connector_svc.test_connection, connector_id)
    except ConnectorNotFoundError:
        raise _not_found(connector_id)
    return _to_out(row)


@router.get(
    "/{connector_id}/tables",
    summary="List queryable tables for a connector",
    response_model=List[str],
)
async def list_connector_tables(connector_id: str) -> List[str]:
    connector_svc = DataConnectorService()
    try:
        return await run_in_threadpool(connector_svc.list_tables, connector_id)
    except ConnectorNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Connector '{connector_id}' not found.")
    except NotImplementedError as exc:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, str(exc))


@router.get(
    "/{connector_id}/tables/preview",
    summary="Preview every table in a data source",
    response_model=ConnectorTablesPreviewResponse,
)
async def preview_connector_tables(
    connector_id: str,
    limit: int = 5,
) -> ConnectorTablesPreviewResponse:
    """
    Runs a capped preview against every table this connector reports, so
    the "Connect a data source" picker can show all of them at once
    instead of one at a time. Each table is previewed independently — one
    slow/broken table shows an inline error, it doesn't fail the others.
    """
    connector_svc = DataConnectorService()
    try:
        table_names = await run_in_threadpool(connector_svc.list_tables, connector_id)
    except ConnectorNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Connector '{connector_id}' not found.")
    except NotImplementedError as exc:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, str(exc))

    results: List[TablePreview] = []
    for table_name in table_names:
        try:
            df = await run_in_threadpool(connector_svc.preview_table, connector_id, table_name, limit)
            preview = df.where(pd.notnull(df), None).to_dict(orient="records")
            results.append(TablePreview(table=table_name, columns=list(df.columns), preview=preview))
        except Exception as exc:
            results.append(TablePreview(table=table_name, error=str(exc)))

    return ConnectorTablesPreviewResponse(tables=results)
