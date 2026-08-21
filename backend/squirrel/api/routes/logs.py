#!/usr/bin/python
"""
Logging Route Module
====================

Endpoints for retrieving request context and recent application logs.
"""
# ------------------------------------------------------------------------------
# Imports

import json
import re
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Query, Request, status

from squirrel.core.request_context import get_request_id_from_request
from squirrel.schemas.auth import LogEntry, RecentLogsResponse, RequestLogContext


router = APIRouter()
LOG_PATTERN = re.compile(r"\[([0-9a-fA-F-]{36})\]")


def _latest_log_file() -> Optional[Path]:
    logs_dir = Path("logs")
    if not logs_dir.exists():
        return None
    log_files = sorted(logs_dir.glob("app_*.log"), key=lambda path: path.stat().st_mtime, reverse=True)
    return log_files[0] if log_files else None


def _parse_log_line(line: str) -> Optional[LogEntry]:
    try:
        payload: Dict[str, Any] = json.loads(line)
    except json.JSONDecodeError:
        return LogEntry(message=line.strip(), raw={"line": line.strip()})

    record = payload.get("record", {})
    time_data = record.get("time", {})
    extra = record.get("extra", {}) or {}
    message = record.get("message", "")
    match = LOG_PATTERN.search(message)

    return LogEntry(
        timestamp=time_data.get("repr"),
        level=(record.get("level") or {}).get("name", "INFO"),
        message=message,
        logger_name=record.get("name"),
        module=record.get("module"),
        function=record.get("function"),
        line=record.get("line"),
        request_id=match.group(1) if match else None,
        extra=extra,
        raw=payload,
    )


@router.get(
    "/request",
    summary="Get the current request log context",
    status_code=status.HTTP_200_OK,
    response_model=RequestLogContext,
)
async def get_request_log_context(request: Request) -> RequestLogContext:
    return RequestLogContext(
        request_id=get_request_id_from_request(request),
        method=request.method,
        path=request.url.path,
        client_host=request.client.host if request.client else None,
    )


@router.get(
    "/recent",
    summary="Get recent application logs",
    status_code=status.HTTP_200_OK,
    response_model=RecentLogsResponse,
)
async def get_recent_logs(
    limit: int = Query(default=50, ge=1, le=200),
) -> RecentLogsResponse:
    log_file = _latest_log_file()
    if log_file is None:
        return RecentLogsResponse(source="logs/app_*.log", limit=limit, entries=[])

    with log_file.open("r", encoding="utf-8") as handle:
        lines = handle.readlines()

    entries = [
        entry for entry in (
            _parse_log_line(line)
            for line in lines[-limit:]
        )
        if entry is not None
    ]

    return RecentLogsResponse(source=str(log_file), limit=limit, entries=entries)