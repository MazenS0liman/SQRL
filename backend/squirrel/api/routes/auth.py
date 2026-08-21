#!/usr/bin/python
"""
Auth Route Module
=================

Endpoints for registering users, authenticating requests, and revoking tokens.
"""
# ------------------------------------------------------------------------------
# Imports

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from squirrel.schemas.auth import (
    AuthTokenResponse,
    AuthUserRead,
    CurrentUserResponse,
    LoginRequest,
    RegisterRequest,
)
from squirrel.services.auth.AuthService import auth_service


router = APIRouter()
bearer_scheme = HTTPBearer(auto_error=False)


def _to_user_response(user) -> AuthUserRead:
    return auth_service.serialize_user(user)


def _get_bearer_token(credentials: HTTPAuthorizationCredentials | None) -> str:
    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return credentials.credentials


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> AuthUserRead:
    token = _get_bearer_token(credentials)
    user = auth_service.get_user_by_token(token)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    return _to_user_response(user)


@router.post(
    "/register",
    summary="Register a user",
    status_code=status.HTTP_201_CREATED,
    response_model=AuthTokenResponse,
)
async def register_user(request: RegisterRequest) -> AuthTokenResponse:
    try:
        user = auth_service.register_user(
            username=request.username,
            email=str(request.email),
            password=request.password,
            full_name=request.full_name,
        )
        token = auth_service.issue_token(user.id)
        return AuthTokenResponse(
            access_token=token.token,
            expires_at=token.expires_at,
            user=_to_user_response(user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post(
    "/login",
    summary="Authenticate a user",
    status_code=status.HTTP_200_OK,
    response_model=AuthTokenResponse,
)
async def login_user(request: LoginRequest) -> AuthTokenResponse:
    try:
        user = auth_service.authenticate(request.identifier, request.password)
        token = auth_service.issue_token(user.id)
        return AuthTokenResponse(
            access_token=token.token,
            expires_at=token.expires_at,
            user=_to_user_response(user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc


@router.get(
    "/me",
    summary="Get the current authenticated user",
    status_code=status.HTTP_200_OK,
    response_model=CurrentUserResponse,
)
async def read_current_user(current_user: AuthUserRead = Depends(get_current_user)) -> CurrentUserResponse:
    return CurrentUserResponse(user=current_user)


@router.post(
    "/logout",
    summary="Revoke the active token",
    status_code=status.HTTP_200_OK,
)
async def logout_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict[str, str]:
    token = _get_bearer_token(credentials)
    auth_service.revoke_token(token)
    return {"detail": "Logged out"}