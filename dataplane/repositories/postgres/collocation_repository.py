"""Ports main/views.py::collocations onto SQL — n=2 fixed. Counts adjacent
pairs straight from main_token (_window_ngram_query.py, n=2); the per-document
DocumentNgram table it read from Phase 5 on was dropped as slower and inexact
(docs/hosting.md §3). Delegates to the same query builder
NgramRepository uses (architecture plan §1), while keeping its own public
interface — collocations have their own product future (e.g.
mutual-information scoring) a generic n-gram interface shouldn't carry."""

from sqlalchemy.ext.asyncio import AsyncSession

from dataplane.repositories.base import CollocationRepository, MatchMode, RankedPage, SortDirection
from dataplane.repositories.postgres._window_ngram_query import compute_ngram_window_page


class PostgresCollocationRepository(CollocationRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def ranked(
        self,
        document_ids: list[int],
        query: str | None,
        match_mode: MatchMode,
        sort: str,
        direction: SortDirection,
        limit: int,
        offset: int,
    ) -> RankedPage:
        return await compute_ngram_window_page(
            self._session, document_ids, 2, query, match_mode, sort, direction, limit, offset
        )
