"""Mirrors main/views.py::ngrams + _get_ngram_size around NgramRepository.
Only real difference from FrequencyService/CollocationService: `n` is
caller-supplied and validated against NGRAM_SIZES, same as the Django view
(an invalid n falls back to the default 3, matching _get_ngram_size's
behavior exactly — not clamped to the nearest valid size, since {2,3,4,5}
is a discrete set, not a range)."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from dataplane.repositories.base import MatchMode, Mode, NgramRepository, RankedRow, SortDirection
from dataplane.repositories.shared import resolve_document_ids
from dataplane.services.pagination import normalize_per_page

NGRAM_SIZES = (2, 3, 4, 5)
DEFAULT_NGRAM_SIZE = 3


@dataclass(frozen=True)
class RankedSearchResult:
    rows: list[RankedRow]
    result_count: int
    type_count: int
    token_total: int
    page: int
    per_page: int
    n: int  # the n ACTUALLY used, after validation — echoed back like KWICService.window


class NgramService:
    def __init__(self, session: AsyncSession, repository: NgramRepository):
        self._session = session
        self._repository = repository

    @staticmethod
    def _normalize_n(n: int) -> int:
        return n if n in NGRAM_SIZES else DEFAULT_NGRAM_SIZE

    async def search(
        self,
        corpus_ids: list[int],
        corrected: bool,
        n: int,
        query: str | None,
        match_mode: MatchMode,
        sort: str,
        direction: SortDirection,
        page: int,
        per_page: int,
    ) -> RankedSearchResult:
        n = self._normalize_n(n)
        per_page = normalize_per_page(per_page)
        page = max(page, 1)
        mode = Mode.CORRECTED if corrected else Mode.ORIGINAL

        document_ids = await resolve_document_ids(self._session, corpus_ids)
        offset = (page - 1) * per_page

        result = await self._repository.ranked(document_ids, mode, n, query, match_mode, sort, direction, per_page, offset)

        return RankedSearchResult(
            rows=result.rows,
            result_count=result.result_count,
            type_count=result.type_count,
            token_total=result.token_total,
            page=page,
            per_page=per_page,
            n=n,
        )
