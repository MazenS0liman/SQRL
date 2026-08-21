#!/usr/bin/python
"""
Authentication and Logging Schemas
===================================

Pydantic models for the user authentication and logging APIs.
"""
# ------------------------------------------------------------------------------
# Imports

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: EmailStr = Field(...)
    password: str = Field(..., min_length=8, max_length=128)
    full_name: Optional[str] = Field(default=None, max_length=120)


class LoginRequest(BaseModel):
    identifier: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=8, max_length=128)


class AuthUserRead(BaseModel):
    id: str
    username: str
    email: EmailStr
    full_name: Optional[str] = None
    created_at: datetime


class AuthTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: AuthUserRead


class CurrentUserResponse(BaseModel):
    user: AuthUserRead


class RequestLogContext(BaseModel):
    request_id: str
    method: str
    path: str
    client_host: Optional[str] = None


class LogEntry(BaseModel):
    timestamp: Optional[str] = None
    level: str = "INFO"
    message: str = ""
    logger_name: Optional[str] = None
    module: Optional[str] = None
    function: Optional[str] = None
    line: Optional[int] = None
    request_id: Optional[str] = None
    extra: Dict[str, Any] = Field(default_factory=dict)
    raw: Optional[Dict[str, Any]] = None


class RecentLogsResponse(BaseModel):
    source: str
    limit: int
    entries: List[LogEntry] = Field(default_factory=list)