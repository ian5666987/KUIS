from fastapi import APIRouter, Depends

from dataplane.core.security import AuthenticatedUser
from dataplane.dependencies import get_current_user

router = APIRouter(tags=["auth"])


@router.get("/api/v1/whoami")
def whoami(user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
    """Phase 1's end-to-end proof that a Django-issued JWT is accepted here
    with no DB or Django call in the request path (architecture plan §3's
    verification step). Not itself a data-plane feature — the first real
    protected endpoint is fast-KWIC in Phase 2."""
    return user
