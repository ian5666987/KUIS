"""
Cross-feature helper, not tied to any one repository — every analysis
feature resolves a corpus selection to document ids the same way. Mirrors
main/views.py::_get_selected_documents, which the Django session drove; here
the selection travels as Next.js URL search params instead (architecture
plan §6), so this takes corpus_ids directly rather than reading a session.

DISTINCT matters here for the identical reason it does in the Django
version: a document in two selected corpora must be counted once, not
twice — see docs/ONBOARDING.md §6 ("Overlap is deduped via .distinct()").

This is also where the secondary metadata filter is applied
(docs/metadata-catalogue-plan.md). Deliberately HERE and nowhere else: the
filter narrows the document set rather than changing any feature's
computation, and all seven endpoints already funnel through this one
function, so none of the repository interfaces, SQL builders or worker
aggregates need to know the filter exists. The same single-mechanism
argument docs/error-analytics-plan.md makes for resolving taxonomy codes
with one LIKE.
"""

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from dataplane.models.tables import corpus_documents, document_metadata
from dataplane.repositories.base import MetadataFilter

# Facet name -> (column, is_numeric). Numeric columns get no `= ''` test:
# comparing an integer column to an empty string is a type error in Postgres.
_FACET_COLUMNS = {
    "university": (document_metadata.c.university, False),
    "year": (document_metadata.c.year, True),
    "grade": (document_metadata.c.grade, True),
}


def _facet_condition(column, values, include_unset, numeric):
    """One facet's predicate, or None when that facet isn't filtered at all.

    None rather than a tautology, following the same convention
    postgres/_error_query.py::resolve_leaf_node_ids uses: the caller can tell
    "no predicate for this facet" apart from "a predicate matching everything".
    """
    parts = []

    if values:
        parts.append(column.in_(list(values)))

    if include_unset:
        # Under the LEFT OUTER JOIN below, IS NULL is true in BOTH required
        # cases — a catalogue row whose field is empty, and a document with no
        # catalogue row at all (the outer join supplied the NULLs). That
        # equivalence is the whole reason this feature needs no second code
        # path for "documents without metadata".
        parts.append(column.is_(None) if numeric else or_(column.is_(None), column == ""))

    if not parts:
        return None
    return parts[0] if len(parts) == 1 else or_(*parts)


def _escape_like(value: str) -> str:
    """Escapes LIKE wildcards so a name code containing % or _ matches
    literally. Backslash first, or it would double-escape the escapes."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _metadata_conditions(metadata_filter: MetadataFilter) -> list:
    """AND across facets, OR within one."""
    conditions = []

    values_by_facet = {
        "university": metadata_filter.universities,
        "year": metadata_filter.years,
        "grade": metadata_filter.grades,
    }

    for name, (column, numeric) in _FACET_COLUMNS.items():
        condition = _facet_condition(
            column,
            values_by_facet[name],
            name in metadata_filter.unset,
            numeric,
        )
        if condition is not None:
            conditions.append(condition)

    if metadata_filter.name_code:
        # Prefix, not substring: a name code is an identifier, and ~400 of
        # them means the useful question is "whose codes start with this".
        conditions.append(
            document_metadata.c.name_code.ilike(
                f"{_escape_like(metadata_filter.name_code)}%", escape="\\"
            )
        )

    return conditions


async def resolve_document_ids(
    session: AsyncSession,
    corpus_ids: list[int],
    metadata_filter: MetadataFilter | None = None,
) -> list[int]:
    """Document ids for a corpus selection, optionally narrowed by metadata.

    `metadata_filter=None` (the default) keeps every existing caller and test
    behaving exactly as before. An empty return is already the established
    "nothing to compute" signal — every query builder guards on it — so a
    filter that matches nothing needs no new short-circuit.
    """
    if not corpus_ids:
        return []

    statement = select(corpus_documents.c.document_id)
    conditions = [corpus_documents.c.corpus_id.in_(corpus_ids)]

    if metadata_filter is not None and not metadata_filter.is_empty:
        # OUTER join, isouter=True: a document with no catalogue row must still
        # be *reachable*, so that "(no value)" can select it. An inner join
        # would silently drop it before any predicate ran.
        statement = statement.select_from(
            corpus_documents.join(
                document_metadata,
                document_metadata.c.document_id == corpus_documents.c.document_id,
                isouter=True,
            )
        )
        conditions.extend(_metadata_conditions(metadata_filter))

    result = await session.execute(statement.where(and_(*conditions)).distinct())
    return [row[0] for row in result.all()]
