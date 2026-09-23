"""GET /api/v1/collocations — ports main/views.py::collocations. Same
shape as routers/frequency.py; see that file's docstring."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from dataplane.core.security import AuthenticatedUser
from dataplane.dependencies import get_collocation_service, get_current_user
from dataplane.repositories.base import MatchMode, SortDirection
from dataplane.schemas.ranked import RankedRowOut, RankedSearchResponse
from dataplane.services.collocation_service import CollocationService

router = APIRouter(prefix="/api/v1", tags=["collocations"])


@router.get("/collocations", response_model=RankedSearchResponse)
async def search_collocations(
    service: Annotated[CollocationService, Depends(get_collocation_service)],
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
