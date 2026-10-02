"""Mirrors main/views.py::collocations's request-shaping around
CollocationRepository — identical shape to FrequencyService, since
collocations differ from word frequency only in which repository (and
therefore which n) answers the query, not in how the request is shaped."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from dataplane.repositories.base import (
    CollocationRepository,
    MatchMode,
    MetadataFilter,
    Mode,
    RankedRow,
    SortDirection,
)
from dataplane.repositories.shared import resolve_document_ids
from dataplane.services.pagination import normalize_per_page


@dataclass(frozen=True)
class RankedSearchResult:
    rows: list[RankedRow]
    result_count: int
    type_count: int
    token_total: int
    page: int
    per_page: int


class CollocationService:
    def __init__(self, session: AsyncSession, repository: CollocationRepository):
        self._session = session
        self._repository = repository

    async def search(
        self,
        corpus_ids: list[int],
        corrected: bool,
        query: str | None,
        match_mode: MatchMode,
        sort: str,
        direction: SortDirection,
        page: int,
        per_page: int,
        metadata_filter: MetadataFilter | None = None,
    ) -> RankedSearchResult:
        per_page = normalize_per_page(per_page)
        page = max(page, 1)
        mode = Mode.CORRECTED if corrected else Mode.ORIGINAL

        # The secondary metadata filter is threaded in here rather than into the
        # repository call: it narrows which documents are in scope, which is a
        # request-shaping concern this layer already owns (see the module
        # docstring), and leaves the repository interface untouched.
        document_ids = await resolve_document_ids(self._session, corpus_ids, metadata_filter)
        offset = (page - 1) * per_page

        result = await self._repository.ranked(document_ids, mode, query, match_mode, sort, direction, per_page, offset)

        return RankedSearchResult(
            rows=result.rows,
            result_count=result.result_count,
            type_count=result.type_count,
            token_total=result.token_total,
            page=page,
            per_page=per_page,
        )
