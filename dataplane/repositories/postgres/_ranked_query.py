"""
Shared query machinery behind FrequencyRepository, CollocationRepository,
and NgramRepository — word frequency (n=1), collocations (n=2 fixed), and
n-grams (n=2-5) are the same query shape at three values of `n`, exactly as
architecture plan §1 anticipated ("CollocationRepository... may still
delegate internally to the same query builder as NgramRepository"). Word
frequency turns out to be the same shape too, at n=1 with no self-join at
all — main/views.py::_count_words already hinted at this by sharing one
function (`size=1` vs `size>=2`) for all three.

Ports main/views.py::_count_words + _rank_counter onto SQL:
  - _count_words builds a Counter of n-length windows per document (never
    crossing a document boundary — docs/ONBOARDING.md §6) via
    `zip(*[words[i:] for i in range(size)])`. The SQL equivalent is an
    N-way self-join on Token, keyed by (document_id, mode), joined on
    position offsets — the same technique
    dataplane/repositories/postgres/kwic_repository.py uses to MATCH a
    specific phrase, generalized here to ENUMERATE every window and
    GROUP BY it instead.
  - _rank_counter's filter -> two-pass-sort -> paginate pipeline becomes a
    filtered/ordered/paginated SELECT over that grouped result.

Ships as a self-join over Token — this WAS the Tier-0 production shape
until Phase 5's WordType/DocumentWordFreq/DocumentNgram tables landed and
the three concrete repositories swapped their internals onto
_aggregate_query.py instead. Kept alive (not deleted) as a correctness
cross-check: dataplane/tests/test_tier_swap_consistency.py asserts Tier 0
and Tier 2 produce IDENTICAL RankedPages for the same query, on the theory
that the swap should be invisible in output, not just in the Service/
Router/Next.js layers plan §1 already promised were untouched.

Phase 5 update: joins WordType for the word string, since Token.word_type
FK replaced Token.word (architecture plan §2, Tier 1) — the self-join
technique itself is unaffected, only which column holds the word.
"""

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dataplane.models.tables import token, word_type
from dataplane.repositories.base import MatchMode, Mode, RankedPage, SortDirection
from dataplane.repositories.postgres._ranked_pagination import paginate_sql_source


def _ngram_source_query(document_ids: list[int], mode: Mode, n: int):
    """(item, count) for every distinct n-length window across the given
    documents — item is the window's words space-joined (a single word when
    n=1). Not yet filtered, sorted, or paginated; see compute_ranked_page."""
    base = token.alias("t0")
    base_wt = word_type.alias("wt0")
    from_clause = base.join(base_wt, base_wt.c.id == base.c.word_type_id)
    word_cols = [base_wt.c.form]

    for i in range(1, n):
        t = token.alias(f"t{i}")
        wt = word_type.alias(f"wt{i}")
        from_clause = from_clause.join(
            t,
            and_(
                t.c.document_id == base.c.document_id,
                t.c.mode == base.c.mode,
                t.c.position == base.c.position + i,
            ),
        ).join(wt, wt.c.id == t.c.word_type_id)
        word_cols.append(wt.c.form)

    item_expr = base_wt.c.form if n == 1 else func.concat_ws(" ", *word_cols)

    return (
        select(item_expr.label("item"), func.count().label("count"))
        .select_from(from_clause)
        .where(and_(base.c.document_id.in_(document_ids), base.c.mode == mode.value))
        .group_by(*word_cols)
    )


async def compute_ranked_page(
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

    source = _ngram_source_query(document_ids, mode, n).subquery()

    # token_total is the raw WORD occurrence count, independent of n — used
    # for "per million tokens" normalization the same way regardless of
    # whether this is frequency, collocations, or 5-grams (main/views.py's
    # _count_words sets `total` from the word list length BEFORE branching
    # on `size`, so a document's token_total is identical across all three
    # features). NOT the same as summing this page's n-gram counts, which
    # would undercount by (n-1) per document — each window needs n
    # consecutive tokens, so the last n-1 positions of every document start
    # no window of length n.
    token_total = (
        await session.execute(
            select(func.count())
            .select_from(token)
            .where(and_(token.c.document_id.in_(document_ids), token.c.mode == mode.value))
        )
    ).scalar_one()

    rows, result_count, type_count = await paginate_sql_source(
        session, source, query, match_mode, sort, direction, limit, offset
    )

    return RankedPage(rows=rows, result_count=result_count, type_count=type_count, token_total=token_total)
