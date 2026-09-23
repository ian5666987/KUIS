"""
GET /api/v1/frequency — ports main/views.py::word_frequency (architecture
plan §4). Any authenticated user (matches the Django view's plain
@login_required, no staff requirement).

Query param names deliberately match the Django view's own GET params
(`q`, `match`, `sort`, `dir`, `page`, `per_page`) for anyone cross-
referencing the two — not a new vocabulary.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from dataplane.core.security import AuthenticatedUser
from dataplane.dependencies import get_current_user, get_frequency_service
from dataplane.repositories.base import MatchMode, SortDirection
from dataplane.schemas.ranked import RankedRowOut, RankedSearchResponse
from dataplane.services.frequency_service import FrequencyService

router = APIRouter(prefix="/api/v1", tags=["frequency"])


@router.get("/frequency", response_model=RankedSearchResponse)
async def search_frequency(
    service: Annotated[FrequencyService, Depends(get_frequency_service)],
    _user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    corpus_ids: Annotated[list[int], Query(min_length=1)],
    q: str | None = None,
    match: MatchMode = MatchMode.CONTAINS,
    sort: str = "count",
    dir: Annotated[SortDirection | None, Query()] = None,
    corrected: bool = False,
    page: int = 1,
    per_page: int = 50,
) -> RankedSearchResponse:
    # main/views.py::_rank_counter's default direction depends on sort
    # ('asc' when sorting by item, 'desc' otherwise) — FastAPI can't express
    # a default conditional on another param declaratively, so it's resolved
    # here instead of in the service, keeping the service's signature a
    # plain required SortDirection like KWICService's window is a plain int.
    direction = dir if dir is not None else (SortDirection.ASC if sort == "item" else SortDirection.DESC)

    result = await service.search(
        corpus_ids=corpus_ids,
        corrected=corrected,
        query=q,
        match_mode=match,
        sort=sort,
        direction=direction,
        page=page,
        per_page=per_page,
    )

    return RankedSearchResponse(
        results=[RankedRowOut(item=row.item, count=row.count) for row in result.rows],
        result_count=result.result_count,
        type_count=result.type_count,
        token_total=result.token_total,
        page=result.page,
        per_page=result.per_page,
    )
