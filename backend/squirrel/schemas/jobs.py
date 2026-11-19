#!/usr/bin/python
"""
Job Schemas
===========

API models for durable background jobs and their event logs.
"""
# ——————————————————————————————————————————————————————————————
# Imports

from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class JobRead(BaseModel):
    """One durable job row."""

    id: str
    owner_user_id: Optional[str] = None
    type: str
    status: str
    progress: int = 0
    payload: Dict[str, Any] = Field(default_factory=dict)
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class JobEventRead(BaseModel):
    """One line in a job's event log."""

    id: int
    job_id: str
    level: str = "info"
    message: str
    data: Optional[Dict[str, Any]] = None
    created_at: Optional[datetime] = None
