"""
Cross-feature helper, not tied to any one repository — every analysis
feature resolves a corpus selection to document ids the same way. Mirrors
main/views.py::_get_selected_documents, which the Django session drove; here
the selection travels as Next.js URL search params instead (architecture
plan §6), so this takes corpus_ids directly rather than reading a session.

DISTINCT matters here for the identical reason it does in the Django
version: a document in two selected corpora must be counted once, not
twice — see docs/ONBOARDING.md §6 ("Overlap is deduped via .distinct()").
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dataplane.models.tables import corpus_documents


async def resolve_document_ids(session: AsyncSession, corpus_ids: list[int]) -> list[int]:
    if not corpus_ids:
        return []

    result = await session.execute(
        select(corpus_documents.c.document_id).where(corpus_documents.c.corpus_id.in_(corpus_ids)).distinct()
    )
    return [row[0] for row in result.all()]
