"""
FastAPI dependencies shared across routers (architecture plan §1/§3).

get_current_user / require_staff: Phase 1. get_db_session / get_kwic_service:
Phase 2, alongside dataplane/models/tables.py and the first repository
implementation. The other four get_<feature>_service factories land as each
repository is implemented (Phase 4/6).
"""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from dataplane.core.db import get_db_session
from dataplane.core.security import AuthenticatedUser, TokenError, decode_access_token
from dataplane.repositories.postgres.kwic_repository import PostgresKWICRepository
from dataplane.services.kwic_service import KWICService

_bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> AuthenticatedUser:
    if credentials is None:
        raise TokenError("Missing Authorization header.")

    return decode_access_token(credentials.credentials)


def require_staff(user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
    """Re-expresses main/views.py::staff_required's exact rule (is_staff
    only, no separate roles/permissions table) as a FastAPI dependency."""
    if not user.is_staff:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Staff access required.")

    return user


def get_kwic_service(session: Annotated[AsyncSession, Depends(get_db_session)]) -> KWICService:
    # The concrete repository is chosen here and nowhere else — this is the
    # one line that changes when KWICRepository grows an OpenSearch
    # implementation (architecture plan §1's central design goal).
    return KWICService(session, PostgresKWICRepository(session))
