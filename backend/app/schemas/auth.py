"""Pydantic schemas for authentication (register / login / token).

Passwords are validated here and never logged or persisted in plaintext.
Tokens are opaque bearer strings; the response shape mirrors OAuth2 resource
owner password flows so standard clients can integrate later.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

#: Username charset: letters, digits, underscore, dot, hyphen. Keeps lookups
#: simple and blocks header-injection style values.
USERNAME_PATTERN = r"^[a-zA-Z0-9_.\-]{3,64}$"

#: Minimum password length. We enforce a floor, not composition rules — long
#: passphrases beat short complex passwords.
PASSWORD_MIN_LEN = 8
PASSWORD_MAX_LEN = 128


class AuthRegisterRequest(BaseModel):
    username: str = Field(pattern=USERNAME_PATTERN)
    password: str = Field(min_length=PASSWORD_MIN_LEN, max_length=PASSWORD_MAX_LEN)
    display_name: str | None = Field(default=None, max_length=128)
    #: Required only when the deployment configures ``AUTH_INVITE_CODE``.
    invite_code: str | None = Field(default=None, max_length=128)


class AuthLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LEN)


class AuthTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Token lifetime in seconds.")


class AuthUserOut(BaseModel):
    """The authenticated identity (never includes the password hash)."""

    id: str
    username: str
    display_name: str
