"""
The filter -> sort -> paginate rules shared by every "ranked list of (item,
count)" feature (architecture plan §4/§5) — extracted here once a second
query source (Tier 2's DocumentWordFreq/DocumentNgram, in _aggregate_query.py)
needed the exact same rules _ranked_query.py's Tier-0 self-join already
implemented. One implementation of the tie-break subtlety (see below),
reused by both tiers, rather than two copies that could silently drift.

Mirrors main/views.py::_rank_counter exactly:
  - sort == "item": plain alphabetical order/reverse.
  - anything else: order by count, with an ALWAYS-ascending alphabetical
    tie-break regardless of direction — Python's stable sort keeps the
    first pass's alphabetical order for equal counts even under
    reverse=True, which "ORDER BY count DESC, item DESC" would get wrong.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dataplane.repositories.base import MatchMode, RankedRow, SortDirection


async def paginate_sql_source(
    session: AsyncSession,
    source,  # a SQLAlchemy Subquery exposing .c.item (text) and .c.count (int)
    query: str | None,
    match_mode: MatchMode,
    sort: str,
    direction: SortDirection,
    limit: int,
    offset: int,
) -> tuple[list[RankedRow], int, int]:
    """For sources where `item` is a real column the database can filter on
    directly (Tier 0's self-join CONCAT, Tier 2's word-frequency JOIN to
    WordType.form). Returns (page_rows, result_count, type_count)."""
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

    if sort == "item":
        order = source.c.item.asc() if direction == SortDirection.ASC else source.c.item.desc()
        filtered = filtered.order_by(order)
    else:
        count_order = source.c.count.asc() if direction == SortDirection.ASC else source.c.count.desc()
        filtered = filtered.order_by(count_order, source.c.item.asc())

    page_rows = (await session.execute(filtered.limit(limit).offset(offset))).all()

    return [RankedRow(item=item, count=count) for item, count in page_rows], result_count, type_count


def paginate_python_rows(
    rows: list[tuple[str, int]],
    query: str | None,
    match_mode: MatchMode,
    sort: str,
    direction: SortDirection,
    limit: int,
    offset: int,
) -> tuple[list[RankedRow], int, int]:
    """For sources where `item` only exists after resolving ids to strings
    in Python — Tier 2's n-grams/collocations, whose DocumentNgram rows
    store word_type_ids (a JSON array), not a filterable string column (see
    _aggregate_query.py). A direct Python port of main/views.py::
    _rank_counter's own two-pass sort, since that function already worked
    this way — the SQL version in this module's paginate_sql_source is what
    had to prove it replicated THIS logic, not the other way around."""
    type_count = len(rows)

    if query:
        term = query.strip().lower()
        if match_mode == MatchMode.STARTS:
            rows = [r for r in rows if r[0].startswith(term)]
        elif match_mode == MatchMode.ENDS:
            rows = [r for r in rows if r[0].endswith(term)]
        elif match_mode == MatchMode.EXACT:
            rows = [r for r in rows if r[0] == term]
        else:
            rows = [r for r in rows if term in r[0]]

    result_count = len(rows)

    rows = sorted(rows, key=lambda r: r[0])
    if sort != "item":
        rows.sort(key=lambda r: r[1], reverse=(direction != SortDirection.ASC))
    elif direction == SortDirection.DESC:
        rows.reverse()

    page = rows[offset : offset + limit]
    return [RankedRow(item=item, count=count) for item, count in page], result_count, type_count
