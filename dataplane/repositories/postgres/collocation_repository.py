"""Ports main/views.py::collocations (_count_words(size=2) + _rank_counter)
onto SQL — n=2 fixed. Delegates to the same query builder NgramRepository
uses (architecture plan §1), while keeping its own public interface —
collocations have their own product future (e.g. mutual-information
scoring) a generic n-gram interface shouldn't be forced to carry."""

from sqlalchemy.ext.asyncio import AsyncSession

from dataplane.repositories.base import CollocationRepository, MatchMode, Mode, RankedPage, SortDirection
from dataplane.repositories.postgres._ranked_query import compute_ranked_page


class PostgresCollocationRepository(CollocationRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def ranked(
        self,
        document_ids: list[int],
        mode: Mode,
        query: str | None,
        match_mode: MatchMode,
        sort: str,
        direction: SortDirection,
        limit: int,
        offset: int,
    ) -> RankedPage:
        return await compute_ranked_page(
            self._session, document_ids, mode, 2, query, match_mode, sort, direction, limit, offset
        )
