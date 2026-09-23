"""
FastAPI dependencies shared across routers (architecture plan §1/§3).

get_current_user / require_staff: Phase 1. get_db_session / get_kwic_service:
Phase 2. get_frequency_service / get_collocation_service / get_ngram_service:
Phase 4, alongside their repositories. get_error_analytics_service is the
one still missing — Phase 6, blocked on the error-type taxonomy.
"""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from dataplane.core.db import get_db_session
from dataplane.core.security import AuthenticatedUser, TokenError, decode_access_token
from dataplane.repositories.postgres.collocation_repository import PostgresCollocationRepository
from dataplane.repositories.postgres.frequency_repository import PostgresFrequencyRepository
from dataplane.repositories.postgres.kwic_repository import PostgresKWICRepository
from dataplane.repositories.postgres.ngram_repository import PostgresNgramRepository
from dataplane.services.collocation_service import CollocationService
from dataplane.services.frequency_service import FrequencyService
from dataplane.services.kwic_service import KWICService
from dataplane.services.ngram_service import NgramService

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
    # one line that changes when a repository grows an OpenSearch/ClickHouse
    # implementation (architecture plan §1's central design goal). Same
    # pattern repeats for every get_<feature>_service below.
    return KWICService(session, PostgresKWICRepository(session))


def get_frequency_service(session: Annotated[AsyncSession, Depends(get_db_session)]) -> FrequencyService:
    return FrequencyService(session, PostgresFrequencyRepository(session))


def get_collocation_service(session: Annotated[AsyncSession, Depends(get_db_session)]) -> CollocationService:
    return CollocationService(session, PostgresCollocationRepository(session))


def get_ngram_service(session: Annotated[AsyncSession, Depends(get_db_session)]) -> NgramService:
    return NgramService(session, PostgresNgramRepository(session))
