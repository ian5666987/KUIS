"""
Query machinery behind PostgresErrorAnalyticsRepository — Error Frequency
and EPIC (Error Phrase In Context) (docs/error-analytics-plan.md).

ErrorFilter.codes (a flat, OR-combined list of taxonomy codes at any
depth — a leaf like "ktinf" or a category like "gramatikal") resolves
against ErrorTaxonomyNode — a small (~90-row) reference table — in ONE
query, before ErrorAnnotation (the large table) is ever touched. For each
code, a `concat(';', path, ';') LIKE '%;code;%'` substring-containment
check matches EITHER a direct leaf hit (a leaf's own path always ends
with its own code as the last segment) OR every descendant of a category
with that code — one mechanism, no separate exact-match branch needed.
Safe regardless of whether `code` is the first, middle, or last path
segment, with zero change to how `path` is stored (that exact
semicolon-joined format is load-bearing for main/corpus_parsing.py's
raw_features -> node resolution, via worker/tasks/error_annotations.py).
Performance is a non-issue: this LIKE scan runs against the tiny taxonomy
table, never against ErrorAnnotation — the resolved node-id set is then
applied there as a plain indexed `taxonomy_node_id IN (...)`. This is also
the "batch processing" the frontend relies on: selecting a whole category
in the tree picker sends just that ONE code, and the whole subtree is
resolved here in a single query rather than the frontend enumerating and
sending every individual leaf code.

Error Frequency queries ErrorAnnotation directly (Tier-0/1), not the
DocumentErrorFreq aggregate — see docs/error-analytics-plan.md Part 3: a
phrase's own text is not a bounded-enough dimension for a per-(document,
phrase) aggregate to meaningfully shrink, the same reasoning EPIC's
occurrences() (and KWIC) already use to stay off Tier-2 entirely.
"""

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from dataplane.models.tables import document, document_error_freq, error_annotation, error_taxonomy_node, token, word_type
from dataplane.repositories.base import (
    ErrorCategoryCount,
    ErrorFilter,
    ErrorFrequencyPage,
    ErrorFrequencyRow,
    ErrorOccurrence,
    ErrorSummary,
    MatchMode,
    SortDirection,
)
from dataplane.repositories.postgres._ranked_pagination import paginate_sql_source


async def resolve_leaf_node_ids(session: AsyncSession, filter: ErrorFilter) -> list[int] | None:
    """None = no filter given — caller applies no taxonomy_node_id
    predicate at all. [] = a filter was given but matched zero leaves —
    caller short-circuits to an empty result without querying
    ErrorAnnotation."""
    if not filter.codes:
        return None

    conditions = [
        error_taxonomy_node.c.is_leaf.is_(True),
        or_(*[func.concat(";", error_taxonomy_node.c.path, ";").like(f"%;{code};%") for code in filter.codes]),
    ]

    result = await session.execute(select(error_taxonomy_node.c.id).where(and_(*conditions)))
    return [row[0] for row in result.all()]


def _base_conditions(document_ids: list[int], node_ids: list[int] | None):
    """`state != 'active'` and unresolved (`taxonomy_node IS NULL`) rows are
    excluded from every analytics read by default — kept (not deleted) in
    ErrorAnnotation for audit/debugging (docs/error-analytics-plan.md), but
    never surfaced here. EPIC's row shape also requires a resolved
    error_code/category_path to display."""
    conditions = [
        error_annotation.c.document_id.in_(document_ids),
        error_annotation.c.state == "active",
        error_annotation.c.taxonomy_node_id.isnot(None),
    ]
    if node_ids is not None:
        conditions.append(error_annotation.c.taxonomy_node_id.in_(node_ids))
    return conditions


async def compute_error_frequency_page(
    session: AsyncSession,
    document_ids: list[int],
    filter: ErrorFilter,
    query: str | None,
    match_mode: MatchMode,
    sort: str,
    direction: SortDirection,
    limit: int,
    offset: int,
) -> ErrorFrequencyPage:
    if not document_ids:
        return ErrorFrequencyPage(rows=[], result_count=0, type_count=0)

    node_ids = await resolve_leaf_node_ids(session, filter)
    if node_ids == []:
        return ErrorFrequencyPage(rows=[], result_count=0, type_count=0)

    source = (
        select(error_annotation.c.original_text.label("item"), func.count().label("count"))
        .where(and_(*_base_conditions(document_ids, node_ids)))
        .group_by(error_annotation.c.original_text)
        .subquery()
    )

    ranked_rows, result_count, type_count = await paginate_sql_source(
        session, source, query, match_mode, sort, direction, limit, offset, case_sensitive=False,
    )

    return ErrorFrequencyPage(
        rows=[ErrorFrequencyRow(phrase=row.item, frequency=row.count) for row in ranked_rows],
        result_count=result_count,
        type_count=type_count,
    )


async def compute_error_occurrences(
    session: AsyncSession, document_ids: list[int], filter: ErrorFilter, window: int, limit: int, offset: int
) -> list[ErrorOccurrence]:
    if not document_ids:
        return []

    node_ids = await resolve_leaf_node_ids(session, filter)
    if node_ids == []:
        return []

    stmt = (
        select(
            error_annotation.c.document_id,
            error_annotation.c.start_position,
            error_annotation.c.end_position,
            error_annotation.c.correction_text,
            error_taxonomy_node.c.code,
            error_taxonomy_node.c.path,
        )
        .select_from(
            error_annotation.join(error_taxonomy_node, error_taxonomy_node.c.id == error_annotation.c.taxonomy_node_id)
        )
        .where(and_(*_base_conditions(document_ids, node_ids)))
        .order_by(error_annotation.c.document_id, error_annotation.c.start_position)
        .limit(limit)
        .offset(offset)
    )
    matches = (await session.execute(stmt)).all()
    if not matches:
        return []

    # Same batched `position BETWEEN` technique as
    # kwic_repository.py::_hydrate_hits — one query for the whole page's
    # context, not one per row.
    range_clauses = [
        and_(
            token.c.document_id == m.document_id,
            token.c.mode == "original",  # an error exists only in the original text by definition
            token.c.position.between(m.start_position - window, m.end_position - 1 + window),
        )
        for m in matches
    ]
    context_rows = (
        await session.execute(
            select(token.c.document_id, token.c.position, word_type.c.form)
            .select_from(token.join(word_type, word_type.c.id == token.c.word_type_id))
            .where(or_(*range_clauses))
            .order_by(token.c.document_id, token.c.position)
        )
    ).all()

    words_by_doc: dict[int, dict[int, str]] = {}
    for doc_id, position, form in context_rows:
        words_by_doc.setdefault(doc_id, {})[position] = form

    doc_ids_on_page = {m.document_id for m in matches}
    titles = dict(
        (await session.execute(select(document.c.id, document.c.title).where(document.c.id.in_(doc_ids_on_page)))).all()
    )

    occurrences = []
    for m in matches:
        doc_words = words_by_doc.get(m.document_id, {})
        occurrences.append(
            ErrorOccurrence(
                document_id=m.document_id,
                document_title=titles.get(m.document_id, ""),
                left=[doc_words[p] for p in range(m.start_position - window, m.start_position) if p in doc_words],
                # Built from Token at [start_position, end_position), not by
                # re-splitting original_text — position-consistent by
                # construction, and naturally empty for a zero-width
                # omission-error span rather than needing that case handled
                # specially.
                keyword=[doc_words[p] for p in range(m.start_position, m.end_position) if p in doc_words],
                right=[doc_words[p] for p in range(m.end_position, m.end_position + window) if p in doc_words],
                error_code=m.code,
                category_path=m.path,
                correction_text=m.correction_text,
            )
        )
    return occurrences


async def count_error_occurrences(session: AsyncSession, document_ids: list[int], filter: ErrorFilter) -> int:
    if not document_ids:
        return 0

    node_ids = await resolve_leaf_node_ids(session, filter)
    if node_ids == []:
        return 0

    result = await session.execute(
        select(func.count()).select_from(error_annotation).where(and_(*_base_conditions(document_ids, node_ids)))
    )
    return result.scalar_one()


async def compute_error_summary(session: AsyncSession, document_ids: list[int]) -> ErrorSummary:
    if not document_ids:
        return ErrorSummary(total=0, by_category=[])

    rows = (
        await session.execute(
            select(error_taxonomy_node.c.top_category, func.sum(document_error_freq.c.count).label("count"))
            .select_from(
                document_error_freq.join(error_taxonomy_node, error_taxonomy_node.c.id == document_error_freq.c.taxonomy_node_id)
            )
            .where(document_error_freq.c.document_id.in_(document_ids))
            .group_by(error_taxonomy_node.c.top_category)
        )
    ).all()

    by_category = [ErrorCategoryCount(category=row.top_category, count=row.count) for row in rows]
    return ErrorSummary(total=sum(c.count for c in by_category), by_category=by_category)
