"""
KWIC service — the layer that stays unchanged when KWICRepository's
Postgres implementation is later swapped for OpenSearch (architecture plan
§1). Owns request-shaping concerns (corpus->document resolution, window
clamping, pagination math) that have nothing to do with which storage
engine answers the query; the repository owns nothing but the query itself.

Mirrors main/views.py::kwic_search + _get_window (window clamping) +
_get_per_page (page-size validation) — same rules, re-expressed statelessly
since there's no Django session driving corpus selection or defaults here.
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from dataplane.repositories.base import KWICHit, KWICRepository, Mode
from dataplane.repositories.shared import resolve_document_ids
from dataplane.services.pagination import normalize_per_page

KWIC_WINDOW_MIN = 2
KWIC_WINDOW_MAX = 10
KWIC_WINDOW_DEFAULT = 5


@dataclass(frozen=True)
class KWICSearchResult:
    hits: list[KWICHit]
    total: int
    window: int  # the CLAMPED window actually used, not necessarily what was requested
    page: int
    per_page: int


class KWICService:
    def __init__(self, session: AsyncSession, repository: KWICRepository):
        self._session = session
        self._repository = repository

    @staticmethod
    def _clamp_window(window: int) -> int:
        return min(max(window, KWIC_WINDOW_MIN), KWIC_WINDOW_MAX)

    async def search(
        self,
        corpus_ids: list[int],
        query: str,
        corrected: bool,
        window: int,
        page: int,
        per_page: int,
    ) -> KWICSearchResult:
        window = self._clamp_window(window)
        per_page = normalize_per_page(per_page)
        page = max(page, 1)

        words = query.strip().lower().split()
        mode = Mode.CORRECTED if corrected else Mode.ORIGINAL

        document_ids = await resolve_document_ids(self._session, corpus_ids)

        if not document_ids or not words:
            return KWICSearchResult(hits=[], total=0, window=window, page=page, per_page=per_page)

        offset = (page - 1) * per_page
        hits = await self._repository.search(document_ids, words, mode, window, per_page, offset)
        total = await self._repository.count_matches(document_ids, words, mode)

        return KWICSearchResult(hits=hits, total=total, window=window, page=page, per_page=per_page)
