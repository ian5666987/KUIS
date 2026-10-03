"""Ports main/views.py::collocations onto SQL — n=2 fixed. Phase 5: swapped
onto Tier-2's DocumentNgram (via _aggregate_query.py, n=2) instead of
Tier-0's raw self-join. Delegates to the same query builder
NgramRepository uses (architecture plan §1), while keeping its own public
interface — collocations have their own product future (e.g.
mutual-information scoring) a generic n-gram interface shouldn't carry."""

from sqlalchemy.ext.asyncio import AsyncSession

from dataplane.repositories.base import CollocationRepository, MatchMode, Mode, RankedPage, SortDirection
from dataplane.repositories.postgres._aggregate_query import compute_ngram_agg_page


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
        return await compute_ngram_agg_page(
            self._session, document_ids, mode, 2, query, match_mode, sort, direction, limit, offset
        )
