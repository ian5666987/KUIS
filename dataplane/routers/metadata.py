"""GET /api/v1/metadata-facets — the values available on each secondary-filter
facet (metadata/*.csv, loaded by `load_metadata_catalogue`), for populating the
filter controls on every analysis page. Any authenticated user, no staff gate,
matching taxonomy.py and error_analytics.py's reasoning.

Global rather than scoped to the caller's corpus selection: a scoped list would
reshuffle the checkboxes every time a corpus is ticked, and
TaxonomyRepository.tree() already set the precedent that filter vocabulary is
reference data. The values are drawn only from catalogue rows with a linked
document, so no offered value is a dead end.

The filter these values feed back into is NOT a param here — it rides along on
each analysis endpoint instead (dataplane/schemas/metadata.py).
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from dataplane.core.security import AuthenticatedUser
from dataplane.dependencies import get_current_user, get_metadata_service
from dataplane.schemas.metadata import MetadataFacetsResponse
from dataplane.services.metadata_service import MetadataService

router = APIRouter(prefix="/api/v1", tags=["metadata"])


@router.get("/metadata-facets", response_model=MetadataFacetsResponse)
async def get_metadata_facets(
    service: Annotated[MetadataService, Depends(get_metadata_service)],
    _user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> MetadataFacetsResponse:
    facets = await service.facets()

    return MetadataFacetsResponse(
        universities=facets.universities,
        years=facets.years,
        grades=facets.grades,
    )
