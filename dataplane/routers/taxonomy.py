"""GET /api/v1/taxonomy — the error-type tree (info/error.xml, loaded by
`load_error_taxonomy`), for populating the filter picker on the Error
Frequency/EPIC pages. Any authenticated user (no staff gate, matches
error_analytics.py's reasoning)."""

from typing import Annotated

from fastapi import APIRouter, Depends

from dataplane.core.security import AuthenticatedUser
from dataplane.dependencies import get_current_user, get_taxonomy_service
from dataplane.schemas.taxonomy import TaxonomyNodeSchema
from dataplane.services.taxonomy_service import TaxonomyService

router = APIRouter(prefix="/api/v1", tags=["taxonomy"])


@router.get("/taxonomy", response_model=list[TaxonomyNodeSchema])
async def get_taxonomy(
    service: Annotated[TaxonomyService, Depends(get_taxonomy_service)],
    _user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> list[TaxonomyNodeSchema]:
    tree = await service.tree()
    return [TaxonomyNodeSchema(**_to_dict(node)) for node in tree]


def _to_dict(node) -> dict:
    return {
        "code": node.code,
        "path": node.path,
        "gloss": node.gloss,
        "is_leaf": node.is_leaf,
        "children": [_to_dict(child) for child in node.children],
    }
