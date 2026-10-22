"""
The n-gram read path behind NgramRepository and CollocationRepository:
counts every n-word window straight from main_token with `lead()`, in one
statement.

Replaces the per-document DocumentNgram table (dropped in migration
0019_drop_document_ngram). That table was measured to be the wrong trade
(docs/hosting.md §3):

  - It was SLOWER than this query at every corpus size measured: it shipped
    every candidate n-gram for the selection to Python to resolve ids, filter
    and sort, where this returns only the page.
  - It was INEXACT: the worker kept the top 500 n-grams per (document, n), so
    a long document's tail was silently dropped from the corpus totals.
  - It was 62% of all corpus storage — 96.9% of its rows had count = 1,
    because n-grams in short essays repeat across documents, not within one.

How the statement is built:

  windows   one row per token: its form plus the next n-1 forms in the same
            document — `lead(form, i) OVER (PARTITION BY document_id ORDER BY
            position)`. The partition is what keeps a window from crossing a
            document boundary (docs/feature-list.md §10), and the last n-1
            tokens of each document, whose window runs off the end, are
            dropped by the IS NOT NULL test. Positions are contiguous from 0
            (main/models.py::build_tokens), so "next row" and "position + i"
            agree — which is why this matches _ranked_query.py's self-join
            exactly (dataplane/tests/test_tier_swap_consistency.py).
  ngrams    GROUP BY the space-joined window.
  filtered  the `q` filter.
  page      ORDER BY + LIMIT/OFFSET.

The outer SELECT reads type_count and result_count from `ngrams` and
`filtered` and LEFT JOINs the page onto a one-row base, so an empty page (a
filter matching nothing, an offset past the end) still returns both counts.
Postgres materializes a CTE referenced more than once, so the GROUP BY runs
once, not once per count.

Same filter and tie-break rules as every other ranked feature — they come
from _ranked_pagination.py, not a copy.
"""

from sqlalchemy import func, literal, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from dataplane.models.tables import document, token, word_type
from dataplane.repositories.base import MatchMode, RankedPage, RankedRow, SortDirection
from dataplane.repositories.postgres._ranked_pagination import match_condition, order_clauses


def _window_ngram_statement(
    document_ids: list[int],
    n: int,
    query: str | None,
    match_mode: MatchMode,
    sort: str,
    direction: SortDirection,
    limit: int,
    offset: int,
):
    following = [
        func.lead(word_type.c.form, i).over(partition_by=token.c.document_id, order_by=token.c.position).label(f"w{i}")
        for i in range(1, n)
    ]
    windows = (
        select(word_type.c.form.label("w0"), *following)
        .select_from(token.join(word_type, word_type.c.id == token.c.word_type_id))
        .where(token.c.document_id.in_(document_ids))
        .cte("windows")
    )

    item = func.concat_ws(" ", *(windows.c[f"w{i}"] for i in range(n)))
    ngrams = (
        select(item.label("item"), func.count().label("count"))
        .where(windows.c[f"w{n - 1}"].is_not(None))
        .group_by(item)
        .cte("ngrams")
    )

    condition = match_condition(ngrams.c.item, query, match_mode)
    filtered = ngrams if condition is None else select(ngrams.c.item, ngrams.c.count).where(condition).cte("filtered")

    page = (
        select(filtered.c.item, filtered.c.count)
        .order_by(*order_clauses(filtered.c.item, filtered.c.count, sort, direction))
        .limit(limit)
        .offset(offset)
        .subquery("page")
    )
    base = select(literal(1).label("one")).subquery("base")

    return (
        select(
            select(func.count()).select_from(ngrams).scalar_subquery().label("type_count"),
            select(func.count()).select_from(filtered).scalar_subquery().label("result_count"),
            page.c.item,
            page.c.count,
        )
        .select_from(base.outerjoin(page, true()))
        # A subquery's ORDER BY isn't guaranteed to survive the join, so the
        # page (at most 250 rows) is ordered again on the way out.
        .order_by(*order_clauses(page.c.item, page.c.count, sort, direction))
    )


async def compute_ngram_window_page(
    session: AsyncSession,
    document_ids: list[int],
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

    # Document.token_count is the exact "total word occurrences" figure (set
    # by worker/tasks/indexing.py), independent of n — see _ranked_query.py's
    # docstring on why token_total is the same for every n.
    token_total = (
        await session.execute(
            select(func.coalesce(func.sum(document.c.token_count), 0)).where(document.c.id.in_(document_ids))
        )
    ).scalar_one()

    result = await session.execute(
        _window_ngram_statement(document_ids, n, query, match_mode, sort, direction, limit, offset)
    )
    rows = result.all()

    return RankedPage(
        rows=[RankedRow(item=row.item, count=row.count) for row in rows if row.item is not None],
        result_count=rows[0].result_count,
        type_count=rows[0].type_count,
        token_total=token_total,
    )
