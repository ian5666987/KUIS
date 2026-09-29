"""Trivial pass-through — the taxonomy tree has no request-shaping to do
(no corpus scope, no pagination), but stays behind a Service/Router split
like every other feature for consistency, and so a future addition (e.g.
caching, since this is near-static reference data) has somewhere to live
without disturbing the router."""

from sqlalchemy.ext.asyncio import AsyncSession

from dataplane.repositories.base import TaxonomyNodeOut, TaxonomyRepository


class TaxonomyService:
    def __init__(self, session: AsyncSession, repository: TaxonomyRepository):
        self._session = session
        self._repository = repository

    async def tree(self) -> list[TaxonomyNodeOut]:
        return await self._repository.tree()
