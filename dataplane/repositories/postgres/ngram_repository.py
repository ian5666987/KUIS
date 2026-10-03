"""Ports main/views.py::ngrams onto SQL — n is caller-supplied (validated to
2-5 by NgramService, matching main/views.py::NGRAM_SIZES). Phase 5: swapped
onto Tier-2's DocumentNgram (via _aggregate_query.py) instead of Tier-0's
raw self-join — see frequency_repository.py's docstring for what "swapped"
means here (nothing above this file changed)."""

from sqlalchemy.ext.asyncio import AsyncSession

from dataplane.repositories.base import MatchMode, Mode, NgramRepository, RankedPage, SortDirection
from dataplane.repositories.postgres._aggregate_query import compute_ngram_agg_page


class PostgresNgramRepository(NgramRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def ranked(
        self,
        document_ids: list[int],
        mode: Mode,
        n: int,
        query: str | None,
        match_mode: MatchMode,
        sort: str,
        direction: SortDirection,
        limit: int,
        offset: int,
    ) -> RankedPage:
        return await compute_ngram_agg_page(
            self._session, document_ids, mode, n, query, match_mode, sort, direction, limit, offset
        )
