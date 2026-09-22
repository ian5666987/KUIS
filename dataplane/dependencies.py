"""
FastAPI dependencies shared across routers (architecture plan §1/§3).

PHASE 1: get_current_user / require_staff. get_db_session and
get_repository(...) land in Phase 2 alongside dataplane/models/tables.py and
the first repository implementation.
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from dataplane.core.security import AuthenticatedUser, TokenError, decode_access_token

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
