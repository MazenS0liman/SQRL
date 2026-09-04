#!/usr/bin/python
"""
MinIO Service Module
=====================

Overview
--------

This module provides MinIO (S3-compatible) service integration capabilities for SQRL.
The MinIOService class enables interaction with a MinIO server or S3-compatible
HTTP API for file uploads, downloads, and storage operations.

Features
--------

- Upload and download operations against an S3-compatible API
- Error handling and logging

"""
# ——————————————————————————————————————————————————————————————
# Imports

# Standard Libraries
import os
import shutil
import tempfile
from enum import Enum
from pathlib import Path
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse
from httpx import Timeout
from typing_extensions import override
from urllib3 import PoolManager, Timeout
from urllib.parse import urlparse, unquote

# MinIO client
from minio import Minio
from minio.error import S3Error

# Abstract
from squirrel.services.storage.blob.IBlobStorageService import IBlobStorageService

# Model
from squirrel.schemas.file import File, FileType

# Logging
from loguru import logger

# ——————————————————————————————————————————————————————————————
# MinIO Endpoint Enum
class MinIOConfig(Enum):
    """
    Enum for MinIO API endpoints.
    """
    ENDPOINT = os.getenv("MINIO_ENDPOINT", "http://localhost:9000")
    ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "admin")
    SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "strongpassword123")
    BUCKET = os.getenv("MINIO_BUCKET", "squirrel")

# ——————————————————————————————————————————————————————————————
# MinIO Service Class
class MinIOService(IBlobStorageService):
    """
    MinIO Service Class
    ---------------------

    A class that provides methods for managing file operations against a
    MinIO HTTP API. Endpoints are sourced from MINIO_*
    environment variables where available.

    **Methods:**
    
    - :meth:`retrieve_file`
        Downloads a file from MinIO.
        
    - :meth:`upload_file`
        Uploads a file to MinIO.
    
    - :meth:`delete_file`
        Deletes a file from MinIO.
    """
    def __init__(self):
        super().__init__()
        # Configure MinIO client
        endpoint = MinIOConfig.ENDPOINT.value
        
        # strip scheme if present for Minio constructor
        parsed = urlparse(endpoint)
        host = parsed.netloc or parsed.path
        secure = parsed.scheme == "https"
        self.client = Minio(
            host,
            access_key=MinIOConfig.ACCESS_KEY.value,
            secret_key=MinIOConfig.SECRET_KEY.value,
            secure=secure,
            http_client=PoolManager(
                timeout=Timeout(connect=5, read=30),
                maxsize=10,
                retries=False,          # don't silently retry 503s
            ),
        )
        
        # Create bucket if it doesn't exist
        self.bucket = MinIOConfig.BUCKET.value
        if not self.client.bucket_exists(self.bucket):
            logger.info(f"Bucket '{self.bucket}' does not exist. Creating it.")
            self.client.make_bucket(self.bucket)

    @override
    def retrieve_file(
        self,
        file_url: str,
    ) -> Optional[File]:
        """
        Download a specific file from MinIO bucket and return it with its raw
        content loaded into ``fileByte``.

        **Description:**

            This method retrieves a file from the MinIO bucket via the API endpoint, handling
            directory structure preservation and authentication.

        .. note::
            ``fileByte`` holds the file's *raw bytes*, exactly as stored —
            never force-decoded as UTF-8 text. This service has no way to know
            whether a given object is text (CSV, JSON) or binary (a joblib
            model, a future parquet/image artifact); every current caller
            either consumes ``fileByte`` as bytes directly (``io.BytesIO(...)``
            for pandas/joblib) or decodes it itself when it knows the content
            is text (see ``WorkspaceService.load_pipeline_artifact``). Forcing
            a UTF-8 decode here previously crashed on any binary object with
            ``UnicodeDecodeError`` (e.g. downloading a ``.joblib`` model for
            prediction) and was never actually correct for the text case either.

        :param file_url: URL of the file to download (e.g., s3://bucket/key)
        :type file_url: str

        :return: File object with raw content in ``fileByte``, or None if retrieval fails
        :rtype: Optional[File]
        """
        try:
            logger.info(f"Retrieving file from MinIO: {file_url}")

            # Expect s3://bucket/key or bucket/key
            url = file_url
            if url.startswith("s3://"):
                url = url.replace("s3://", "", 1)

            parts = url.split("/", 1)
            if len(parts) < 2:
                raise ValueError(f"Invalid S3 path format: {file_url}")
            bucket, object_name = parts[0], parts[1]

            with tempfile.NamedTemporaryFile(
                delete=False, suffix=Path(object_name).suffix
            ) as tmp:
                local_path = Path(tmp.name)

            try:
                self.client.fget_object(bucket, object_name, str(local_path))
                file_bytes = local_path.read_bytes()

                return File(
                    fileUrl=file_url,
                    fileName=Path(object_name).name,
                    fileByte=file_bytes,
                    size=local_path.stat().st_size,
                    uploadedAt=datetime.fromtimestamp(local_path.stat().st_mtime),
                )
            finally:
                local_path.unlink(missing_ok=True)
        except S3Error as e:
            logger.error(f"MinIO error retrieving '{object_name if 'object_name' in locals() else file_url}' from bucket '{bucket if 'bucket' in locals() else '?'}': {e.code} — {e}")
            return None
        except Exception as e:
            logger.exception(f"Failed to retrieve file from MinIO: {e}")
            return None

    @override
    def upload_file(
        self,
        file_path: str,
        object_name: str,
        metadata: Optional[dict[str, str]] = None,
    ) -> Optional[File]:
        if not object_name:
            raise ValueError("object_name is required")
        
        try:
            upload_file = Path(file_path)
            if not upload_file.exists():
                raise FileNotFoundError(f"File not found: {file_path}")

            file_bytes = upload_file.read_bytes()
            md5_hex = self._calculate_file_md5(file_bytes)

            # Check if object with the same name already exists
            try:
                stat = self.client.stat_object(self.bucket, object_name)
                existing_md5 = (stat.metadata or {}).get("x-amz-meta-md5")
                if existing_md5 == md5_hex:
                    logger.info(f"Identical file already exists in MinIO, skipping upload: {object_name}")
                    return File(
                        fileUrl=f"s3://{self.bucket}/{object_name}",
                        fileName=object_name,
                        size=upload_file.stat().st_size,
                        uploadedAt=datetime.fromtimestamp(upload_file.stat().st_mtime),
                    )
                else:
                    logger.info(f"File with same name but different content found, overwriting: {object_name}")
            except S3Error as e:
                if e.code != "NoSuchKey":
                    raise

            # Upload with md5 stored as user metadata
            self.client.fput_object(
                self.bucket,
                object_name,
                str(upload_file),
                metadata={
                    "x-amz-meta-md5": md5_hex,
                    **(metadata or {}),
                },
            )
            logger.info(f"Uploaded file to MinIO: {object_name} (md5={md5_hex})")

            return File(
                fileUrl=f"s3://{self.bucket}/{object_name}",
                fileName=object_name,
                size=upload_file.stat().st_size,
                uploadedAt=datetime.fromtimestamp(upload_file.stat().st_mtime),
            )
        except S3Error as e:
            logger.error(f"MinIO error uploading object: {e}")
            return None
        except Exception as e:
            logger.exception(f"Failed to upload file to MinIO: {e}")
            return None

    def delete_file(
        self,
        file_url: str
    ) -> bool:
        """
        Delete a specific file from MinIO bucket.

        :param file_url: URL of the file to delete (e.g., s3://bucket/key)
        :type file_url: str
        
        :return: True if deletion was successful, False otherwise
        :rtype: bool
        """
        try:
            logger.info(f"Deleting file from MinIO: {file_url}")

            # Expect s3://bucket/key or bucket/key
            url = file_url
            if url.startswith("s3://"):
                url = url.replace("s3://", "", 1)

            parts = url.split("/", 1)
            if len(parts) < 2:
                raise ValueError(f"Invalid S3 path format: {file_url}")
            bucket, object_name = parts[0], parts[1]

            self.client.remove_object(bucket, object_name)
            return True
        except S3Error as e:
            logger.error(f"MinIO error deleting object: {e}")
            return False
        except Exception as e:
            logger.exception(f"Failed to delete file from MinIO: {e}")
            return False
        
    