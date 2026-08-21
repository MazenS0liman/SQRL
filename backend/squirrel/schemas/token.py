#!/usr/bin/python
"""
Token Schemas
=============

Request/response models for user-managed provider tokens (e.g. Gemini,
Groq, OpenRouter, HuggingFace keys the user supplies from the frontend).
"""
# ------------------------------------------------------------------------------
# Imports

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator

# ------------------------------------------------------------------------------
# Allowed providers - extend as new integrations are added

ALLOWED_PROVIDERS = {
    "gemini",
    "groq",
    "openrouter",
    "huggingface",
    "github",
    "custom",
}


class TokenCreateRequest(BaseModel):
    provider: str = Field(..., description="Provider key, e.g. 'gemini', 'groq'")
    label: Optional[str] = Field(None, max_length=64, description="Optional friendly name")
    token: str = Field(..., min_length=8, max_length=4096)

    @field_validator("provider")
    @classmethod
    def _validate_provider(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in ALLOWED_PROVIDERS:
            raise ValueError(f"Unsupported provider '{value}'")
        return normalized

    @field_validator("token")
    @classmethod
    def _validate_token(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Token cannot be empty")
        return stripped


class TokenRead(BaseModel):
    id: str
    provider: str
    label: Optional[str]
    token_preview: str = Field(..., description="Masked token, e.g. 'sk-or-...932ba5'")
    created_at: datetime
    last_used_at: Optional[datetime]

    model_config = {"from_attributes": True}


class TokenListResponse(BaseModel):
    tokens: list[TokenRead]


class TokenCreateResponse(BaseModel):
    token: TokenRead