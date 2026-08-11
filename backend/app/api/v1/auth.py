"""Authentication router: register and login.

Both endpoints are public (no auth dependency). Responses never carry the
password hash. Errors are returned as 401 with a stable ``reason`` so clients
can branch on it without the server leaking which usernames exist
(``invalid_credentials`` covers both unknown user and wrong password).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_db_session
from app.core.config import get_settings
from app.schemas.auth import (
    AuthLoginRequest,
    AuthRegisterRequest,
    AuthTokenResponse,
    AuthUserOut,
)
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])

_ERROR_STATUS: dict[str, int] = {
    "invalid_credentials": status.HTTP_401_UNAUTHORIZED,
    "auth_not_configured": status.HTTP_503_SERVICE_UNAVAILABLE,
    "invite_code_required": status.HTTP_400_BAD_REQUEST,
    "invite_code_invalid": status.HTTP_400_BAD_REQUEST,
    "username_taken": status.HTTP_409_CONFLICT,
}


def _http_error(err: auth_service.AuthError) -> HTTPException:
    return HTTPException(
        status_code=_ERROR_STATUS.get(err.reason, status.HTTP_400_BAD_REQUEST),
        detail={"reason": err.reason},
    )


@router.post("/register", response_model=AuthUserOut, status_code=201)
def register(
    payload: AuthRegisterRequest,
    db: Session = Depends(get_db_session),
) -> AuthUserOut:
    """Create a new user. Invite-code gated when ``AUTH_INVITE_CODE`` is set."""
    try:
        return auth_service.register(
            db,
            username=payload.username,
            password=payload.password,
            display_name=payload.display_name,
            invite_code=payload.invite_code,
        )
    except auth_service.AuthError as err:
        raise _http_error(err) from err


@router.post("/login", response_model=AuthTokenResponse)
def login(
    payload: AuthLoginRequest,
    db: Session = Depends(get_db_session),
) -> AuthTokenResponse:
    """Exchange credentials for a signed bearer token."""
    try:
        _, token = auth_service.login(db, username=payload.username, password=payload.password)
    except auth_service.AuthError as err:
        raise _http_error(err) from err
    settings = get_settings()
    return AuthTokenResponse(access_token=token, expires_in=settings.auth_token_ttl_minutes * 60)
