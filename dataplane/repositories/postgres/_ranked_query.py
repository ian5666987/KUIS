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

Ships against the raw Token.word column (Tier 0 shape) — this file is
exactly what Phase 5's WordType/DocumentNgram migration replaces
internally, with the Frequency/Collocation/Ngram interfaces and everything
above them (Service, Router, Next.js) unchanged.
"""

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dataplane.models.tables import token
from dataplane.repositories.base import MatchMode, Mode, RankedPage, RankedRow, SortDirection


def _ngram_source_query(document_ids: list[int], mode: Mode, n: int):
    """(item, count) for every distinct n-length window across the given
    documents — item is the window's words space-joined (a single word when
    n=1). Not yet filtered, sorted, or paginated; see compute_ranked_page."""
    base = token.alias("t0")
    from_clause = base
    word_cols = [base.c.word]

    for i in range(1, n):
        t = token.alias(f"t{i}")
        from_clause = from_clause.join(
            t,
            and_(
                t.c.document_id == base.c.document_id,
                t.c.mode == base.c.mode,
                t.c.position == base.c.position + i,
            ),
        )
        word_cols.append(t.c.word)

    item_expr = base.c.word if n == 1 else func.concat_ws(" ", *word_cols)

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

    type_count = (await session.execute(select(func.count()).select_from(source))).scalar_one()

    filtered = select(source.c.item, source.c.count)

    if query:
        term = query.strip().lower()
        if match_mode == MatchMode.STARTS:
            filtered = filtered.where(source.c.item.startswith(term))
        elif match_mode == MatchMode.ENDS:
            filtered = filtered.where(source.c.item.endswith(term))
        elif match_mode == MatchMode.EXACT:
            filtered = filtered.where(source.c.item == term)
        else:
            filtered = filtered.where(source.c.item.contains(term))

    result_count = (await session.execute(select(func.count()).select_from(filtered.subquery()))).scalar_one()

    # Mirrors main/views.py::_rank_counter's two-pass sort exactly: sort by
    # item is a plain alphabetical order/reverse; any other sort orders by
    # count with an ALWAYS-ascending alphabetical tie-break (Python's stable
    # sort keeps the first pass's alphabetical order for equal counts
    # regardless of reverse=True/False — see this file's test suite for the
    # case that would silently regress if this were "ORDER BY count DESC,
    # item DESC" instead).
    if sort == "item":
        order = source.c.item.asc() if direction == SortDirection.ASC else source.c.item.desc()
        filtered = filtered.order_by(order)
    else:
        count_order = source.c.count.asc() if direction == SortDirection.ASC else source.c.count.desc()
        filtered = filtered.order_by(count_order, source.c.item.asc())

    page_rows = (await session.execute(filtered.limit(limit).offset(offset))).all()

    return RankedPage(
        rows=[RankedRow(item=item, count=count) for item, count in page_rows],
        result_count=result_count,
        type_count=type_count,
        token_total=token_total,
    )
