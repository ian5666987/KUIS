"""
Stateless JWT verification (architecture plan §3). No DB or Django call per
request — Django and FastAPI share JWT_SECRET (the same .env, same repo,
same deploy) purely so this can decode independently.

djangorestframework-simplejwt signs with HS256 by default (config/settings.py
SIMPLE_JWT['ALGORITHM']) and PyJWT is what it uses internally, so decoding
here needs no extra library beyond PyJWT itself.
"""

from dataclasses import dataclass

import jwt
from fastapi import HTTPException, status

from dataplane.core.config import settings


@dataclass(frozen=True)
class AuthenticatedUser:
    id: int
    username: str
    email: str
    is_staff: bool


class TokenError(HTTPException):
    def __init__(self, detail: str):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )


def decode_access_token(token: str) -> AuthenticatedUser:
    if not settings.jwt_secret:
        # Fails loudly and specifically rather than letting jwt.decode fail
        # signature verification against an empty key, which would look like
        # "every token is invalid" instead of "JWT_SECRET isn't configured".
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="JWT_SECRET is not configured on the data plane.",
        )

    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise TokenError("Access token has expired.")
    except jwt.InvalidTokenError:
        raise TokenError("Invalid access token.")

    if payload.get("token_type") != "access":
        # Rejects a refresh token presented as an access token — SimpleJWT
        # tags both with the same signature scheme, only token_type differs.
        raise TokenError("Expected an access token.")

    try:
        return AuthenticatedUser(
            id=int(payload["user_id"]),
            username=payload["username"],
            email=payload["email"],
            is_staff=bool(payload["is_staff"]),
        )
    except (KeyError, ValueError):
        raise TokenError("Access token is missing expected claims.")
