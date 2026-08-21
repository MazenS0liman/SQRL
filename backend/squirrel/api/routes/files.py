#!/usr/bin/python
"""
Files Route Module
==================
...
"""
# ——————————————————————————————————————————————————————————————
# Imports

# Standard Libraries
import uuid
import tempfile
from pathlib import Path

# FastAPI
from fastapi import APIRouter, Body, Depends, File as FastAPIFile, HTTPException, UploadFile, status

# Models
from squirrel.schemas.file import File as StorageFile
from squirrel.schemas.file import FileRequest, RenameFileRequest, FileResponse, FileType

# Services
from squirrel.services.storage.blob.MinIOService import MinIOService
from squirrel.services.file.FileService import (
    FileService,
    FileRecordNotFound,
    FileInUseError,
)

# Schema
from squirrel.schemas.file import File, FilesResponse

# Auth
from squirrel.api.routes.auth import get_current_user
from squirrel.schemas.auth import AuthUserRead

# Settings
from squirrel.core.config import settings

# Logging
from loguru import logger

# API Router
router = APIRouter()

# ——————————————————————————————————————————————————————————————
# Endpoints

@router.post(
    "/retrieve",
    summary="Send a file retrieval request to the files endpoint",
    status_code=status.HTTP_200_OK,
    description="Send a file retrieval request to the files endpoint and receive a response.",
    response_description="The response from the files endpoint, including any generated content or files.",
    response_model=FileResponse
)
async def retrieve_file(
    request: FileRequest = Body(
        ...,
        description="The file request payload containing the file reference and optional parameters."
    ),
    current_user: AuthUserRead = Depends(get_current_user),
) -> FileResponse:
    """
    Retrieve File Endpoint
    ----------------------
    Only files recorded as belonging to ``current_user`` in
    ``file`` may be retrieved — this keeps the endpoint from
    being usable to fetch arbitrary MinIO objects belonging to other
    users just by knowing/guessing their ``fileUrl``.
    """
    storage_service = MinIOService()
    file_service = FileService()
    output_files: dict[str, StorageFile] = {}

    for index, file_item in enumerate(request.file):
        file_url = str(file_item.fileUrl)
        if not file_service.is_owned_by(file_url, current_user.id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"File '{file_item.fileName or file_url}' not found.",
            )
        retrieved = storage_service.retrieve_file(file_url)
        if retrieved is None:
            continue
        output_files[file_item.fileName or f"file_{index}"] = retrieved

    return FileResponse(success=bool(output_files), outputFiles=output_files or None)


@router.post(
    "/upload",
    summary="Upload a file to MinIO",
    status_code=status.HTTP_200_OK,
    description="Upload a file to MinIO and return the stored file metadata.",
    response_description="The response from the storage backend, including any generated file metadata.",
    response_model=FileResponse
)
async def upload_file(
    file: UploadFile = FastAPIFile(..., description="The file to be uploaded"),
    file_type: FileType = FileType.CSV,
    current_user: AuthUserRead = Depends(get_current_user),
) -> FileResponse:
    storage_service = MinIOService()
    file_service = FileService()

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(file.filename or "upload").suffix) as tmp:
            temp_path = Path(tmp.name)
            while chunk := await file.read(1024 * 1024):
                tmp.write(chunk)

        original_name = file.filename or temp_path.name
        object_name = f"{current_user.id}/{uuid.uuid4().hex[:8]}_{original_name}"

        uploaded = storage_service.upload_file(file_path=str(temp_path), object_name=object_name)
        if uploaded is None:
            return FileResponse(success=False, outputFiles=None)

        uploaded.fileName = original_name  # display name stays clean of the namespace prefix
        if file_type is not None:
            uploaded.fileType = file_type

        file_service.record_upload(uploaded, owner_user_id=current_user.id)

        return FileResponse(success=True, outputFiles={original_name: uploaded})
    finally:
        await file.close()
        if "temp_path" in locals() and temp_path.exists():
            temp_path.unlink(missing_ok=True)


@router.post(
    "/upload-multiple",
    summary="Upload multiple files to MinIO",
    status_code=status.HTTP_200_OK,
    description="Upload multiple files to MinIO and return the stored file metadata.",
    response_description="The response from the storage backend, including any generated file metadata.",
    response_model=FileResponse
)
async def upload_multiple_files(
    files: list[UploadFile] = FastAPIFile(..., description="The files to be uploaded"),
    current_user: AuthUserRead = Depends(get_current_user),
) -> FileResponse:
    """
    Upload Multiple Files Endpoint
    ------------------------------
    """
    storage_service = MinIOService()
    file_service = FileService()
    output_files: dict[str, StorageFile] = {}

    for index, upload in enumerate(files):
        file_type: str = Path(upload.filename or "upload").suffix.lstrip(".").lower()
        try:
            with tempfile.NamedTemporaryFile(
                delete=False, 
                suffix=Path(upload.filename or "upload").suffix
            ) as tmp:
                temp_path = Path(tmp.name)
                while chunk := await upload.read(1024 * 1024):
                    tmp.write(chunk)

            original_name = upload.filename or temp_path.name
            object_name = f"{current_user.id}/{uuid.uuid4().hex[:8]}_{original_name}"

            uploaded = storage_service.upload_file(file_path=str(temp_path), object_name=object_name)
            if uploaded is None:
                continue

            uploaded.fileName = original_name
            if file_type is not None:
                uploaded.fileType = file_type

            file_service.record_upload(uploaded, owner_user_id=current_user.id)
            output_files[original_name] = uploaded
        finally:
            await upload.close()
            if "temp_path" in locals() and temp_path.exists():
                temp_path.unlink(missing_ok=True)

    return FileResponse(success=bool(output_files), outputFiles=output_files or None)


@router.get(
    "/",
    summary="List all uploaded files",
    status_code=status.HTTP_200_OK,
    description="Return metadata for every file the current user has uploaded, whether or not it is attached to a workspace or notebook.",
    response_model=FilesResponse,
)
async def list_files(current_user: AuthUserRead = Depends(get_current_user)) -> FilesResponse:
    file_service = FileService()
    files = file_service.list_files(current_user.id)
    return FilesResponse(success=True, files=files)

@router.patch(
    "/by-url",
    summary="Rename a file by its storage URL",
    status_code=status.HTTP_200_OK,
    response_model=File,
)
async def rename_file_by_url(
    file_url: str,
    body: RenameFileRequest = Body(...),
    current_user: AuthUserRead = Depends(get_current_user),
) -> File:
    """
    Rename a file record by ``file_url``. Delegates the ownership check,
    the update, and propagation into ``sqrl_notebook_data_sources`` to
    ``FileService.rename_file``.
    """
    trimmed = (body.fileName or "").strip()
    if not trimmed:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="fileName must not be empty.")

    file_service = FileService()
    try:
        return file_service.rename_file(file_url, current_user.id, trimmed)
    except FileRecordNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found or not owned by this user.",
        )


@router.delete(
    "/by-url",
    summary="Delete a file by its storage URL",
    status_code=status.HTTP_200_OK,
)
async def delete_file(
    file_url: str,
    current_user: AuthUserRead = Depends(get_current_user),
):
    """
    Delete a file, but only if it is not attached to a workspace or notebook.

    Ownership + attachment checks and the DB row deletion live in
    ``FileService.delete_file``; this route is only responsible for the
    MinIO blob removal, since FileService intentionally doesn't own a
    storage client. The attachment check below runs before the storage
    delete so an attached file's blob is never removed.
    """
    file_service = FileService()
    storage_service = MinIOService()

    # Fail fast on ownership/attachment before touching blob storage.
    try:
        record = file_service.get_owned_file(file_url, current_user.id)
        if record is None:
            raise FileRecordNotFound(file_url)
        if record.workspaceIds or record.notebookIds:
            raise FileInUseError(file_url)
    except FileRecordNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found.")
    except FileInUseError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This file is attached to a workspace or notebook and can't be deleted. "
                   "Remove it from there first.",
        )

    deleted_from_storage = storage_service.delete_file(file_url)
    if not deleted_from_storage:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete file from storage.",
        )

    try:
        file_service.delete_file(file_url, current_user.id)
    except (FileRecordNotFound, FileInUseError) as exc:
        # Extremely unlikely race (record changed between the check above
        # and now), but surface it rather than silently succeeding.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"File state changed during delete: {exc}",
        )

    return {"success": True}
