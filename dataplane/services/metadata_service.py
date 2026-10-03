"""Pass-through for the secondary-filter facets — no request-shaping to do (no
corpus scope, no pagination), but kept behind the Service/Router split like
every other feature, for the same reason taxonomy_service.py is: consistency,
and somewhere for a future addition (caching this near-static reference data)
to live without disturbing the router.

Note what is NOT here: parsing the filter itself. That happens in
dataplane/schemas/metadata.py::MetadataFilterParams.to_filter(), called from
each router — the filter travels with every analysis request, not with this
endpoint.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from dataplane.repositories.base import MetadataFacets, MetadataRepository


class MetadataService:
    def __init__(self, session: AsyncSession, repository: MetadataRepository):
        self._session = session
        self._repository = repository

    async def facets(self) -> MetadataFacets:
        return await self._repository.facets()
