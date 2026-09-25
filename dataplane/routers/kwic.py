"""
GET /api/v1/kwic — Phase 2's proof case for the whole repository-swap
architecture (architecture plan §4/§8). Any authenticated user may search
(matches main/views.py::kwic_search's plain @login_required, no
@staff_required) — see dataplane/dependencies.py::get_current_user.

`sort` restores legacy `kwic`'s sort-by-position control (KwicSort:
center/left/right/document), dropped in the original Phase 2 merge and
reinstated once KUIS-FE split back into "KWIC (Legacy)"/"KWIC (Fast)" pages.
Declared as the KwicSort enum (not a plain str) so an unrecognized value is
a 422, not a silent fallback — main/views.py::_sort_kwic's silent
fallback-to-center for any unrecognized string was never a deliberate
design choice, just an unvalidated `request.GET.get(...)`, so it isn't
reproduced here.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from dataplane.core.security import AuthenticatedUser
from dataplane.dependencies import get_current_user, get_kwic_service
from dataplane.repositories.base import KwicSort
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
    sort: KwicSort = KwicSort.CENTER,
) -> KWICSearchResponse:
    result = await service.search(
        corpus_ids=corpus_ids,
        query=q,
        corrected=corrected,
        window=window,
        page=page,
        per_page=per_page,
        sort=sort,
    )

    return KWICSearchResponse(
        results=[KWICHitOut(**hit.__dict__) for hit in result.hits],
        result_count=result.total,
        page=result.page,
        per_page=result.per_page,
        window=result.window,
        corrected=corrected,
        sort=result.sort.value,
    )
