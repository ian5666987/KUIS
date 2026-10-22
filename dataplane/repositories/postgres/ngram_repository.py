"""Ports main/views.py::ngrams onto SQL — n is caller-supplied (validated to
2-5 by NgramService, matching main/views.py::NGRAM_SIZES). Counts windows
straight from main_token in one statement (_window_ngram_query.py). Phase 5
had swapped this onto the per-document DocumentNgram table; that table was
dropped as slower and inexact (docs/hosting.md §3) — and, as with every
swap here, nothing above this file changed."""

from sqlalchemy.ext.asyncio import AsyncSession

from dataplane.repositories.base import MatchMode, NgramRepository, RankedPage, SortDirection
from dataplane.repositories.postgres._window_ngram_query import compute_ngram_window_page


class PostgresNgramRepository(NgramRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def ranked(
        self,
        document_ids: list[int],
        n: int,
        query: str | None,
        match_mode: MatchMode,
        sort: str,
        direction: SortDirection,
        limit: int,
        offset: int,
    ) -> RankedPage:
        return await compute_ngram_window_page(
            self._session, document_ids, n, query, match_mode, sort, direction, limit, offset
        )
