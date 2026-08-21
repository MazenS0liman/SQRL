#!/usr/bin/python
"""
Abstract Storage Service Module
===============================

Overview
--------

This module provides abstract service integration capabilities for SQRL .
The AbstractStorageService class defines the interface for interacting with different storage backends.

Features
--------

- Upload and download operations
- Error handling and logging

"""
# ——————————————————————————————————————————————————————————————
# Imports
from __future__ import annotations

# Standard Libraries
import zipfile
import hashlib
from pathlib import Path
from datetime import datetime
from abc import ABC, abstractmethod
from typing import Optional, Dict, List

# Models
from squirrel.schemas.file import FileType, File

# Logging
from loguru import logger

# ——————————————————————————————————————————————————————————————
# MinIO Service Class
class IBlobStorageService(ABC):
    """
    Abstract Storage Service Class
    -------------------------------

    This abstract class defines the interface for storage services in SQRL. 
    It provides method signatures for uploading and downloading files, which must be implemented by any concrete storage service class.

    **Methods:**
    
    - :meth:`retrieve_file`
        Downloads a file from the storage backend.
        
    - :meth:`upload_file`
        Uploads a file to the storage backend.
    
    """
    def __init__(self):
        super().__init__()

    @staticmethod
    def _get_file_type(
        extension: str
    ) -> FileType:
        """
        Determine FileType from file extension.
        
        **Description:**
        
            This method maps file extensions to corresponding FileType enum values.
        
        :param extension: File extension (e.g., 'pdf', 'docx')
        :type extension: str
        
        :return: Corresponding FileType enum value
        :rtype: FileType
        """
        extension = extension.lower()

        # Map extensions to FileType enum
        type_mapping = {
            '.pdf': FileType.PDF,
            '.xlsx': FileType.XLSX,
            '.xls': FileType.XLSX,
            '.csv': FileType.CSV,
            '.doc': FileType.DOCX,
            '.docx': FileType.DOCX,
            '.json': FileType.JSON,
            '.md': FileType.MD,
            '.html': FileType.HTML,
            '.htm': FileType.HTML,
            '.png': FileType.PNG,
            '.jpg': FileType.JPG,
            '.jpeg': FileType.JPG,
            '.gif': FileType.GIF,
        }

        return type_mapping.get(extension, FileType.UNKNOWN)

    @staticmethod
    def _calculate_file_md5(
        file_byte: bytes
    ) -> str:
        """
        Calculate the MD5 hash of a file.
        
        **Description:**
        
            This method computes MD5 hash of file content in chunks for memory efficiency.

        :param file_byte: The file content as bytes for which to calculate the MD5 hash
        :type file_byte: bytes

        :return: MD5 hash as hexadecimal string, empty string if calculation fails
        :rtype: str
        """
        md5_hash = hashlib.md5()
        try:
            md5_hash.update(file_byte)
            return md5_hash.hexdigest()
        except Exception as e:
            logger.exception(f"Failed to calculate MD5 for file: {e}")
            return ""

    @staticmethod
    def _extract_zip_metadata(
        zip_path: str
    ) -> Dict[str, str]:
        """
        Extract metadata from the ZIP archive.
        
        **Description:**
        
            This method extracts file information including name, type, size, creation date,
            and MD5 hash for each file in the ZIP archive.

        :param zip_path: Path to the ZIP file
        :type zip_path: str
        
        :return: Dictionary mapping filename to metadata (size, md5, type, created_at)
        :rtype: Dict[str, str]
        """
        metadata = {}

        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                for file_info in zip_ref.filelist:
                    # Skip directories
                    if file_info.is_dir():
                        continue

                    file_name: str = Path(file_info.filename).name
                    file_size: int = file_info.file_size

                    # Calculate MD5 hash of the file content
                    with zip_ref.open(file_info.filename) as f:
                        md5_hash = hashlib.md5()
                        for chunk in iter(lambda: f.read(4096), b""):
                            md5_hash.update(chunk)

                    metadata[file_name] = {
                        "name": file_name,
                        "type": IBlobStorageService._get_file_type(Path(file_name).suffix),
                        "size": file_info.file_size,
                        "created_at": datetime(*file_info.date_time),
                        "md5": md5_hash.hexdigest()
                    }

        except Exception as e:
            logger.exception(f"Failed to extract ZIP metadata: {e}")

        return metadata

    @abstractmethod
    def retrieve_file(
        self,
        file_url: str
    ) -> Optional[File]:
        """
        Abstract method to download a file from the storage backend.

        :param file_url: URL of the file to download
        :type file_url: str

        :return: File object representing the downloaded file, or None if retrieval fails
        :rtype: Optional[File]
        """
        pass
    
    @abstractmethod
    def upload_file(
        self,
        file_path: str,
        object_name: str | None = None
    ) -> Optional[File]:
        """
        Abstract method to upload a file to the storage backend.

        :param file_path: Local path of the file to upload
        :type file_path: str

        :return: File object representing the uploaded file, or None if upload fails
        :rtype: Optional[File]
        """
        pass


