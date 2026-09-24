"""Ports main/views.py::word_frequency onto SQL. Phase 5: swapped onto
Tier-2's DocumentWordFreq (via _aggregate_query.py) instead of Tier-0's raw
self-join (_ranked_query.py) — the repository-swap the architecture plan's
whole layering exists to make invisible above this file. FrequencyService,
the router, and Next.js needed zero changes for this swap."""

from sqlalchemy.ext.asyncio import AsyncSession

from dataplane.repositories.base import FrequencyRepository, MatchMode, Mode, RankedPage, SortDirection
from dataplane.repositories.postgres._aggregate_query import compute_word_freq_page


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
        return await compute_word_freq_page(
            self._session, document_ids, mode, query, match_mode, sort, direction, limit, offset
        )
