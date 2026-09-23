"""Ports main/views.py::word_frequency (_count_words(size=1) + _rank_counter)
onto SQL — see _ranked_query.py for the shared implementation; this is the
n=1 case."""

from sqlalchemy.ext.asyncio import AsyncSession

from dataplane.repositories.base import FrequencyRepository, MatchMode, Mode, RankedPage, SortDirection
from dataplane.repositories.postgres._ranked_query import compute_ranked_page


class PostgresFrequencyRepository(FrequencyRepository):
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
            self._session, document_ids, mode, 1, query, match_mode, sort, direction, limit, offset
        )
