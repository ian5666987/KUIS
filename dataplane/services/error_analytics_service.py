"""Error Frequency + EPIC (Error Phrase In Context) service — the layer
that stays unchanged when ErrorAnalyticsRepository's Postgres
implementation is later swapped, same reasoning as kwic_service.py/
frequency_service.py. Owns request-shaping (corpus->document resolution,
pagination math, building ErrorFilter from the raw code list) that has
nothing to do with which storage engine answers the query."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from dataplane.repositories.base import (
    ErrorAnalyticsRepository,
    ErrorFilter,
    ErrorFrequencyRow,
    ErrorOccurrence,
    ErrorSummary,
    MatchMode,
    SortDirection,
)
from dataplane.repositories.shared import resolve_document_ids
from dataplane.services.kwic_service import KWIC_WINDOW_MAX, KWIC_WINDOW_MIN
from dataplane.services.pagination import normalize_per_page


@dataclass(frozen=True)
class ErrorFrequencySearchResult:
    rows: list[ErrorFrequencyRow]
    result_count: int
    type_count: int
    page: int
    per_page: int


@dataclass(frozen=True)
class EpicSearchResult:
    hits: list[ErrorOccurrence]
    total: int
    window: int  # the CLAMPED window actually used, not necessarily what was requested
    page: int
    per_page: int


class ErrorAnalyticsService:
    def __init__(self, session: AsyncSession, repository: ErrorAnalyticsRepository):
        self._session = session
        self._repository = repository

    async def frequency(
        self,
        corpus_ids: list[int],
        codes: list[str] | None,
        query: str | None,
        match_mode: MatchMode,
        sort: str,
        direction: SortDirection,
        page: int,
        per_page: int,
    ) -> ErrorFrequencySearchResult:
        per_page = normalize_per_page(per_page)
        page = max(page, 1)

        document_ids = await resolve_document_ids(self._session, corpus_ids)
        offset = (page - 1) * per_page

        result = await self._repository.frequency(
            document_ids, ErrorFilter(codes=codes or None), query, match_mode, sort, direction, per_page, offset,
        )

        return ErrorFrequencySearchResult(
            rows=result.rows, result_count=result.result_count, type_count=result.type_count, page=page, per_page=per_page,
        )

    async def occurrences(
        self,
        corpus_ids: list[int],
        codes: list[str] | None,
        window: int,
        page: int,
        per_page: int,
    ) -> EpicSearchResult:
        window = min(max(window, KWIC_WINDOW_MIN), KWIC_WINDOW_MAX)
        per_page = normalize_per_page(per_page)
        page = max(page, 1)

        document_ids = await resolve_document_ids(self._session, corpus_ids)
        offset = (page - 1) * per_page
        filter = ErrorFilter(codes=codes or None)

        hits = await self._repository.occurrences(document_ids, filter, window, per_page, offset)
        total = await self._repository.count_occurrences(document_ids, filter)

        return EpicSearchResult(hits=hits, total=total, window=window, page=page, per_page=per_page)

    async def summary(self, corpus_ids: list[int]) -> ErrorSummary:
        document_ids = await resolve_document_ids(self._session, corpus_ids)
        return await self._repository.summary(document_ids)
