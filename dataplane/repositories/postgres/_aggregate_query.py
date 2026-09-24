"""
Tier-2 read path (architecture plan §2/§5) — the query builder
FrequencyRepository/CollocationRepository/NgramRepository swap onto once
the worker populates DocumentWordFreq/DocumentNgram, replacing
_ranked_query.py's Tier-0 self-join. Same public repository interfaces,
same RankedPage shape — nothing above the repository layer changes, which
is the entire point of the swap (proving the pattern the eventual
ClickHouse/OpenSearch move depends on).

Word frequency and n-grams take genuinely different shapes here, unlike
Tier 0 where one self-join served both:
  - DocumentWordFreq stores a plain word_type_id per row, joinable straight
    to WordType.form — filter/sort/paginate stays fully in SQL
    (_ranked_pagination.py's paginate_sql_source).
  - DocumentNgram stores word_type_ids as a JSON array (see
    main/models.py's DocumentNgram docstring on why) — there is no
    filterable string column until the ids are resolved to forms, so that
    resolution happens in Python after a SQL-side cross-document SUM, and
    filter/sort/paginate runs in Python too
    (_ranked_pagination.py's paginate_python_rows). Top-K-per-document
    (architecture plan §2) means this is a bounded materialization, not an
    unbounded one.
"""

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dataplane.models.tables import document, document_ngram, document_word_freq, word_type
from dataplane.repositories.base import MatchMode, Mode, RankedPage, SortDirection
from dataplane.repositories.postgres._ranked_pagination import paginate_python_rows, paginate_sql_source


async def compute_word_freq_page(
    session: AsyncSession,
    document_ids: list[int],
    mode: Mode,
    query: str | None,
    match_mode: MatchMode,
    sort: str,
    direction: SortDirection,
    limit: int,
    offset: int,
) -> RankedPage:
    if not document_ids:
        return RankedPage(rows=[], result_count=0, type_count=0, token_total=0)

    # Document.token_count/token_count_corrected are already the exact
    # "total word occurrences" figure (set by worker/tasks/indexing.py) —
    # cheaper than re-deriving it from Token, and it's the SAME number
    # regardless of which tier answers the query (see _ranked_query.py's
    # docstring on why token_total is n-independent).
    count_column = document.c.token_count if mode == Mode.ORIGINAL else document.c.token_count_corrected
    token_total = (
        await session.execute(select(func.coalesce(func.sum(count_column), 0)).where(document.c.id.in_(document_ids)))
    ).scalar_one()

    source = (
        select(word_type.c.form.label("item"), func.sum(document_word_freq.c.count).label("count"))
        .select_from(document_word_freq.join(word_type, word_type.c.id == document_word_freq.c.word_type_id))
        .where(and_(document_word_freq.c.document_id.in_(document_ids), document_word_freq.c.mode == mode.value))
        .group_by(word_type.c.form)
        .subquery()
    )

    rows, result_count, type_count = await paginate_sql_source(
        session, source, query, match_mode, sort, direction, limit, offset
    )

    return RankedPage(rows=rows, result_count=result_count, type_count=type_count, token_total=token_total)


async def compute_ngram_agg_page(
    session: AsyncSession,
    document_ids: list[int],
    mode: Mode,
    n: int,
    query: str | None,
    match_mode: MatchMode,
    sort: str,
    direction: SortDirection,
    limit: int,
    offset: int,
) -> RankedPage:
    if not document_ids:
        return RankedPage(rows=[], result_count=0, type_count=0, token_total=0)

    count_column = document.c.token_count if mode == Mode.ORIGINAL else document.c.token_count_corrected
    token_total = (
        await session.execute(select(func.coalesce(func.sum(count_column), 0)).where(document.c.id.in_(document_ids)))
    ).scalar_one()

    # Cross-document SUM stays in SQL — only the id->form resolution and
    # the filter/sort/paginate that depends on it happen in Python. The
    # candidate set is bounded by the worker's top-K-per-document (see
    # worker/tasks/aggregates.py), not by the corpus's full token count.
    aggregated = (
        select(document_ngram.c.word_type_ids, func.sum(document_ngram.c.count).label("count"))
        .where(
            and_(
                document_ngram.c.document_id.in_(document_ids),
                document_ngram.c.mode == mode.value,
                document_ngram.c.n == n,
            )
        )
        .group_by(document_ngram.c.word_type_ids)
    )
    candidates = (await session.execute(aggregated)).all()

    if not candidates:
        return RankedPage(rows=[], result_count=0, type_count=0, token_total=token_total)

    all_ids = {word_type_id for ids, _ in candidates for word_type_id in ids}
    form_rows = (await session.execute(select(word_type.c.id, word_type.c.form).where(word_type.c.id.in_(all_ids)))).all()
    forms_by_id = dict(form_rows)

    string_rows = [(" ".join(forms_by_id[i] for i in ids), count) for ids, count in candidates]

    rows, result_count, type_count = paginate_python_rows(
        string_rows, query, match_mode, sort, direction, limit, offset
    )

    return RankedPage(rows=rows, result_count=result_count, type_count=type_count, token_total=token_total)
