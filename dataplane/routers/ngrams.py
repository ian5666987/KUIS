"""GET /api/v1/ngrams — ports main/views.py::ngrams. Same shape as
routers/frequency.py, plus `n` (see NgramService for the validation/
fallback rule)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from dataplane.core.security import AuthenticatedUser
from dataplane.dependencies import get_current_user, get_ngram_service
from dataplane.repositories.base import MatchMode, SortDirection
from dataplane.schemas.ranked import NgramSearchResponse, RankedRowOut
from dataplane.services.ngram_service import NgramService

router = APIRouter(prefix="/api/v1", tags=["ngrams"])


@router.get("/ngrams", response_model=NgramSearchResponse)
async def search_ngrams(
    service: Annotated[NgramService, Depends(get_ngram_service)],
    _user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    corpus_ids: Annotated[list[int], Query(min_length=1)],
    n: int = 3,
    q: str | None = None,
    match: MatchMode = MatchMode.CONTAINS,
    sort: str = "count",
    dir: Annotated[SortDirection | None, Query()] = None,
    corrected: bool = False,
    page: int = 1,
    per_page: int = 50,
) -> NgramSearchResponse:
    direction = dir if dir is not None else (SortDirection.ASC if sort == "item" else SortDirection.DESC)

    result = await service.search(
        corpus_ids=corpus_ids,
        corrected=corrected,
        n=n,
        query=q,
        match_mode=match,
        sort=sort,
        direction=direction,
        page=page,
        per_page=per_page,
    )

    return NgramSearchResponse(
        results=[RankedRowOut(item=row.item, count=row.count) for row in result.rows],
        result_count=result.result_count,
        type_count=result.type_count,
        token_total=result.token_total,
        page=result.page,
        per_page=result.per_page,
        n=result.n,
    )
