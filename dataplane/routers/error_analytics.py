"""
GET /api/v1/error-frequency, /api/v1/epic, /api/v1/error-summary —
docs/error-analytics-plan.md. Any authenticated user may search (no staff
gate — matches KWIC/Frequency/Collocations/N-grams; no precedent in this
codebase for treating error data as more sensitive than raw corpus text).

`error_code`/`category` are repeated query params, matching the existing
`corpus_ids` convention: `?error_code=ktinf&error_code=ejk&category=
gramatikal`. error_code (OR) is the main filter — one or more specific
leaf error codes; category (AND) is the secondary filter — one or more
taxonomy hierarchy codes at any depth, narrowing the match to leaves under
ALL of them.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from dataplane.core.security import AuthenticatedUser
from dataplane.dependencies import get_current_user, get_error_analytics_service
from dataplane.repositories.base import MatchMode, SortDirection
from dataplane.schemas.error_analytics import (
    ErrorCategoryCountOut,
    ErrorFrequencyResponse,
    ErrorFrequencyRowOut,
    ErrorOccurrenceOut,
    ErrorSummaryResponse,
    EpicSearchResponse,
)
from dataplane.services.error_analytics_service import ErrorAnalyticsService

router = APIRouter(prefix="/api/v1", tags=["error-analytics"])


@router.get("/error-frequency", response_model=ErrorFrequencyResponse)
async def search_error_frequency(
    service: Annotated[ErrorAnalyticsService, Depends(get_error_analytics_service)],
    _user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    corpus_ids: Annotated[list[int], Query(min_length=1)],
    error_code: Annotated[list[str] | None, Query()] = None,
    category: Annotated[list[str] | None, Query()] = None,
    q: str | None = None,
    match: MatchMode = MatchMode.CONTAINS,
    sort: str = "count",
    dir: Annotated[SortDirection | None, Query()] = None,
    page: int = 1,
    per_page: int = 50,
) -> ErrorFrequencyResponse:
    # Same conditional-default resolution as routers/frequency.py's `dir`
    # (FastAPI can't express a default conditional on another param
    # declaratively).
    direction = dir if dir is not None else (SortDirection.ASC if sort == "item" else SortDirection.DESC)

    result = await service.frequency(
        corpus_ids=corpus_ids,
        error_codes=error_code,
        categories=category,
        query=q,
        match_mode=match,
        sort=sort,
        direction=direction,
        page=page,
        per_page=per_page,
    )

    return ErrorFrequencyResponse(
        results=[ErrorFrequencyRowOut(phrase=row.phrase, frequency=row.frequency) for row in result.rows],
        result_count=result.result_count,
        type_count=result.type_count,
        page=result.page,
        per_page=result.per_page,
    )


@router.get("/epic", response_model=EpicSearchResponse)
async def search_epic(
    service: Annotated[ErrorAnalyticsService, Depends(get_error_analytics_service)],
    _user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    corpus_ids: Annotated[list[int], Query(min_length=1)],
    error_code: Annotated[list[str] | None, Query()] = None,
    category: Annotated[list[str] | None, Query()] = None,
    window: int = 5,
    page: int = 1,
    per_page: int = 50,
) -> EpicSearchResponse:
    result = await service.occurrences(
        corpus_ids=corpus_ids,
        error_codes=error_code,
        categories=category,
        window=window,
        page=page,
        per_page=per_page,
    )

    return EpicSearchResponse(
        results=[ErrorOccurrenceOut(**hit.__dict__) for hit in result.hits],
        result_count=result.total,
        page=result.page,
        per_page=result.per_page,
        window=result.window,
    )


@router.get("/error-summary", response_model=ErrorSummaryResponse)
async def get_error_summary(
    service: Annotated[ErrorAnalyticsService, Depends(get_error_analytics_service)],
    _user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    corpus_ids: Annotated[list[int], Query(min_length=1)],
) -> ErrorSummaryResponse:
    result = await service.summary(corpus_ids)

    return ErrorSummaryResponse(
        total=result.total,
        by_category=[ErrorCategoryCountOut(category=c.category, count=c.count) for c in result.by_category],
    )
