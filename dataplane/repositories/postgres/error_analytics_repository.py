"""Postgres implementation of ErrorAnalyticsRepository — thin wiring over
_error_query.py's query builders, same shape as frequency_repository.py's
one-line delegation to _aggregate_query.py."""

from sqlalchemy.ext.asyncio import AsyncSession

from dataplane.repositories.base import (
    ErrorAnalyticsRepository,
    ErrorFilter,
    ErrorFrequencyPage,
    ErrorOccurrence,
    ErrorSummary,
    MatchMode,
    SortDirection,
)
from dataplane.repositories.postgres._error_query import (
    compute_error_frequency_page,
    compute_error_occurrences,
    compute_error_summary,
    count_error_occurrences,
)


class PostgresErrorAnalyticsRepository(ErrorAnalyticsRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def frequency(
        self,
        document_ids: list[int],
        filter: ErrorFilter,
        query: str | None,
        match_mode: MatchMode,
        sort: str,
        direction: SortDirection,
        limit: int,
        offset: int,
    ) -> ErrorFrequencyPage:
        return await compute_error_frequency_page(
            self._session, document_ids, filter, query, match_mode, sort, direction, limit, offset
        )

    async def occurrences(
        self, document_ids: list[int], filter: ErrorFilter, window: int, limit: int, offset: int
    ) -> list[ErrorOccurrence]:
        return await compute_error_occurrences(self._session, document_ids, filter, window, limit, offset)

    async def count_occurrences(self, document_ids: list[int], filter: ErrorFilter) -> int:
        return await count_error_occurrences(self._session, document_ids, filter)

    async def summary(self, document_ids: list[int]) -> ErrorSummary:
        return await compute_error_summary(self._session, document_ids)
