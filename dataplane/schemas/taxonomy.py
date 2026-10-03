"""Wire-format response model for GET /api/v1/taxonomy."""

from __future__ import annotations

from pydantic import BaseModel


class TaxonomyNodeSchema(BaseModel):
    code: str
    path: str
    gloss: str
    is_leaf: bool
    children: list[TaxonomyNodeSchema] = []
