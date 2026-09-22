"""
GET /api/v1/kwic — Phase 2's proof case for the whole repository-swap
architecture (architecture plan §4/§8). Any authenticated user may search
(matches main/views.py::kwic_search's plain @login_required, no
@staff_required) — see dataplane/dependencies.py::get_current_user.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from dataplane.core.security import AuthenticatedUser
from dataplane.dependencies import get_current_user, get_kwic_service
from dataplane.schemas.kwic import KWICHitOut, KWICSearchResponse
from dataplane.services.kwic_service import KWICService

router = APIRouter(prefix="/api/v1", tags=["kwic"])


@router.get("/kwic", response_model=KWICSearchResponse)
async def search_kwic(
    service: Annotated[KWICService, Depends(get_kwic_service)],
    _user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    corpus_ids: Annotated[list[int], Query(min_length=1)],
    q: Annotated[str, Query(min_length=1)],
    corrected: bool = False,
    window: int = 5,
    page: int = 1,
    per_page: int = 50,
) -> KWICSearchResponse:
    result = await service.search(
        corpus_ids=corpus_ids,
        query=q,
        corrected=corrected,
        window=window,
        page=page,
        per_page=per_page,
    )

    return KWICSearchResponse(
        results=[KWICHitOut(**hit.__dict__) for hit in result.hits],
        result_count=result.total,
        page=result.page,
        per_page=result.per_page,
        window=result.window,
        corrected=corrected,
    )
