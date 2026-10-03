"""Postgres implementation of MetadataRepository — serves the distinct values
available on each secondary-filter facet, for populating the filter controls
(docs/metadata-catalogue-plan.md).

Reads DocumentMetadata, Django-owned reference data loaded by
`load_metadata_catalogue` from metadata/*.csv. Read-only, same "Django owns,
FastAPI reads" rule every other Django-owned table here follows — the direct
counterpart of taxonomy_repository.py, which does the same job for the error
tree.

Note this is the only place in the dataplane that reads document_metadata for
its own sake; everywhere else it exists purely as a join target inside
repositories/shared.py.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dataplane.models.tables import document_metadata
from dataplane.repositories.base import MetadataFacets, MetadataRepository


class PostgresMetadataRepository(MetadataRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def facets(self) -> MetadataFacets:
        values = {}

        for name, column in (
            ("universities", document_metadata.c.university),
            ("years", document_metadata.c.year),
            ("grades", document_metadata.c.grade),
        ):
            rows = await self._session.execute(
                select(column)
                .where(
                    # Only facet values that can actually produce a result: an
                    # entry with no linked document contributes nothing to any
                    # query, so offering its value would hand the user a
                    # guaranteed-empty filter.
                    document_metadata.c.document_id.isnot(None),
                    column.isnot(None),
                )
                .distinct()
                .order_by(column)
            )
            values[name] = [row[0] for row in rows.all() if row[0] != ""]

        return MetadataFacets(
            universities=values["universities"],
            years=values["years"],
            grades=values["grades"],
        )
