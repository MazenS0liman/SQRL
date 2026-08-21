#!/usr/bin/python
"""
Token Route Module
===================

Endpoints letting an authenticated user save, list, and delete their own
provider tokens (LLM API keys, etc.) for later reuse. Mirrors the auth
route's dependency pattern.
"""
# ------------------------------------------------------------------------------
# Imports

from fastapi import APIRouter, Depends, HTTPException, status

from squirrel.api.routes.auth import get_current_user
from squirrel.schemas.auth import AuthUserRead
from squirrel.schemas.token import (
    TokenCreateRequest,
    TokenCreateResponse,
    TokenListResponse,
)
from squirrel.services.auth.TokenService import token_service


router = APIRouter()


@router.post(
    "",
    summary="Save a provider token for the current user",
    status_code=status.HTTP_201_CREATED,
    response_model=TokenCreateResponse,
)
async def add_token(
    request: TokenCreateRequest,
    current_user: AuthUserRead = Depends(get_current_user),
) -> TokenCreateResponse:
    try:
        saved = token_service.add_token(
            user_id=current_user.id,
            provider=request.provider,
            token=request.token,
            label=request.label,
        )
        return TokenCreateResponse(token=saved)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc


@router.get(
    "",
    summary="List the current user's saved tokens (masked)",
    status_code=status.HTTP_200_OK,
    response_model=TokenListResponse,
)
async def list_tokens(current_user: AuthUserRead = Depends(get_current_user)) -> TokenListResponse:
    tokens = token_service.list_tokens(current_user.id)
    return TokenListResponse(tokens=tokens)


@router.delete(
    "/{token_id}",
    summary="Delete a saved token",
    status_code=status.HTTP_200_OK,
)
async def delete_token(
    token_id: str,
    current_user: AuthUserRead = Depends(get_current_user),
) -> dict[str, str]:
    deleted = token_service.delete_token(current_user.id, token_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token not found")
    return {"detail": "Token deleted"}