
# ——————————————————————————————————————————————————————————————
# Imports
from __future__ import annotations

# Standard Libraries
import httpx
import boto3
import tempfile
from typing import List
from pathlib import Path

# Config
from squirrel.core.config import settings

# Logging
from loguru import logger

# ──────────────────────────────────────────────────────────────────────────
# Utility Functions
# ──────────────────────────────────────────────────────────────────────────
async def download_files(
    file_urls: List[str]
) -> List[str]:
    file_paths: List[str] = []
    for file_url in file_urls:
        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(file_url).suffix) as temp_file:
            if file_url.startswith("s3://"):
                s3 = boto3.client(
                    "s3",
                    endpoint_url=settings.MINIO_ENDPOINT,
                    aws_access_key_id=settings.MINIO_ACCESS_KEY,
                    aws_secret_access_key=settings.MINIO_SECRET_KEY,
                    region_name="us-east-1",
                )
                bucket, key = file_url[5:].split("/", 1)
                s3.download_file(bucket, key, temp_file.name)

            elif file_url.startswith("http://") or file_url.startswith("https://"):
                async with httpx.AsyncClient() as client:
                    response = await client.get(file_url)
                    response.raise_for_status()
                    temp_file.write(response.content)

            else:
                import shutil
                shutil.copy(file_url, temp_file.name)

            file_paths.append(temp_file.name)
            logger.info("Downloaded {} to {}", file_url, temp_file.name)

    return file_paths

def cleanup(
    paths: List[str]
) -> None:
    """
    Delete temporary local files silently.
    """
    for p in paths:
        try:
            Path(p).unlink(missing_ok=True)
        except Exception as exc:
            logger.warning("Cleanup failed for {}: {}", p, exc)