"""
Tier-2 read path for word frequency (architecture plan §2/§5) — the query
builder FrequencyRepository swapped onto once the worker populates
DocumentWordFreq, replacing _ranked_query.py's Tier-0 self-join. Same public
repository interface, same RankedPage shape — nothing above the repository
layer changed, which was the entire point of the swap (proving the pattern
the eventual ClickHouse/OpenSearch move depends on).

DocumentWordFreq stores a plain word_type_id per row, joinable straight to
WordType.form, so filter/sort/paginate stays fully in SQL
(_ranked_pagination.py's paginate_sql_source).

N-grams and collocations used to have a second builder here over the
per-document DocumentNgram table. That table was dropped (migration
0019_drop_document_ngram); they now count windows straight from main_token —
see _window_ngram_query.py for why.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dataplane.models.tables import document, document_word_freq, word_type
from dataplane.repositories.base import MatchMode, RankedPage, SortDirection
from dataplane.repositories.postgres._ranked_pagination import paginate_sql_source


async def compute_word_freq_page(
    session: AsyncSession,
    document_ids: list[int],
    query: str | None,
    match_mode: MatchMode,
    sort: str,
    direction: SortDirection,
    limit: int,
    offset: int,
) -> RankedPage:
    if not document_ids:
        return RankedPage(rows=[], result_count=0, type_count=0, token_total=0)

    # Document.token_count is already the exact
    # "total word occurrences" figure (set by worker/tasks/indexing.py) —
    # cheaper than re-deriving it from Token, and it's the SAME number
    # regardless of which tier answers the query (see _ranked_query.py's
    # docstring on why token_total is n-independent).
    token_total = (
        await session.execute(
            select(func.coalesce(func.sum(document.c.token_count), 0)).where(document.c.id.in_(document_ids))
        )
    ).scalar_one()

    source = (
        select(word_type.c.form.label("item"), func.sum(document_word_freq.c.count).label("count"))
        .select_from(document_word_freq.join(word_type, word_type.c.id == document_word_freq.c.word_type_id))
        .where(document_word_freq.c.document_id.in_(document_ids))
        .group_by(word_type.c.form)
        .subquery()
    )

    rows, result_count, type_count = await paginate_sql_source(
        session, source, query, match_mode, sort, direction, limit, offset
    )

    return RankedPage(rows=rows, result_count=result_count, type_count=type_count, token_total=token_total)

