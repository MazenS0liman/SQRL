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
    ChangePasswordRequest,
    CurrentUserResponse,
    LoginRequest,
    RegisterRequest,
    UpdateProfileRequest,
)
from squirrel.services.auth.AuthService import auth_service
from squirrel.services.usage.context import set_usage_user_id


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
    # Stamp the user onto the request context so every LLM call made while
    # handling this request is attributed to them in the usage ledger.
    set_usage_user_id(user.id)
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


@router.patch(
    "/me",
    summary="Update the current user's profile",
    status_code=status.HTTP_200_OK,
    response_model=CurrentUserResponse,
)
async def update_current_user(
    request: UpdateProfileRequest,
    current_user: AuthUserRead = Depends(get_current_user),
) -> CurrentUserResponse:
    if request.full_name is None and request.email is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide a display name or an email to update",
        )

    try:
        user = auth_service.update_profile(
            current_user.id,
            full_name=request.full_name,
            email=str(request.email) if request.email is not None else None,
        )
    except ValueError as exc:
        detail = str(exc)
        code = (
            status.HTTP_409_CONFLICT
            if "already in use" in detail
            else status.HTTP_404_NOT_FOUND
        )
        raise HTTPException(status_code=code, detail=detail) from exc

    return CurrentUserResponse(user=_to_user_response(user))


@router.post(
    "/change-password",
    summary="Change the current user's password",
    status_code=status.HTTP_200_OK,
    response_model=AuthTokenResponse,
)
async def change_password(
    request: ChangePasswordRequest,
    current_user: AuthUserRead = Depends(get_current_user),
) -> AuthTokenResponse:
    """Rotate the password and every active session.

    All existing tokens are revoked so a stolen session cannot survive a
    password change, then a fresh token is issued for this client.
    """
    try:
        user = auth_service.change_password(
            current_user.id,
            request.current_password,
            request.new_password,
        )
    except ValueError as exc:
        detail = str(exc)
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in detail.lower()
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=code, detail=detail) from exc

    auth_service.revoke_all_tokens(current_user.id)
    token = auth_service.issue_token(current_user.id)
    return AuthTokenResponse(
        access_token=token.token,
        expires_at=token.expires_at,
        user=_to_user_response(user),
    )


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