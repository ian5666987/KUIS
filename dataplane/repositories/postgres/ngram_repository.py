"""Ports main/views.py::ngrams (_count_words(size=n) + _rank_counter) onto
SQL — n is caller-supplied (main/views.py::NGRAM_SIZES restricts it to
2-5 in the UI; that validation belongs in the service layer, not here —
this repository is structurally happy with any n >= 1)."""

from sqlalchemy.ext.asyncio import AsyncSession

from dataplane.repositories.base import MatchMode, Mode, NgramRepository, RankedPage, SortDirection
from dataplane.repositories.postgres._ranked_query import compute_ranked_page


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
        return await compute_ranked_page(
            self._session, document_ids, mode, n, query, match_mode, sort, direction, limit, offset
        )
