"""Postgres implementation of TaxonomyRepository — reads ErrorTaxonomyNode
(Django-owned reference data, loaded by `load_error_taxonomy` from
info/error.xml) and assembles it into a nested tree. Read-only: nothing
here writes this table, same "Django owns, FastAPI reads" rule every other
Django-owned table in this app already follows."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dataplane.models.tables import error_taxonomy_node
from dataplane.repositories.base import TaxonomyNodeOut, TaxonomyRepository


class PostgresTaxonomyRepository(TaxonomyRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def tree(self) -> list[TaxonomyNodeOut]:
        rows = (
            await self._session.execute(
                select(
                    error_taxonomy_node.c.id,
                    error_taxonomy_node.c.code,
                    error_taxonomy_node.c.parent_id,
                    error_taxonomy_node.c.path,
                    error_taxonomy_node.c.gloss,
                    error_taxonomy_node.c.is_leaf,
                )
            )
        ).all()

        children_by_parent_id: dict[int | None, list] = {}
        for row in rows:
            children_by_parent_id.setdefault(row.parent_id, []).append(row)

        def build(row) -> TaxonomyNodeOut:
            return TaxonomyNodeOut(
                code=row.code,
                path=row.path,
                gloss=row.gloss or "",
                is_leaf=row.is_leaf,
                children=[build(child) for child in children_by_parent_id.get(row.id, [])],
            )

        # The literal root ('eror') isn't itself a meaningful filter
        # choice, so the returned list starts one level down, at the 4 top
        # categories (leksikal/gramatikal/ejaan/lainnya) — each already
        # carrying its own subtree.
        root_row = next((row for row in rows if row.parent_id is None), None)
        if root_row is None:
            return []

        return [build(child) for child in children_by_parent_id.get(root_row.id, [])]
