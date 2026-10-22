"""
The filter -> sort -> paginate rules shared by every "ranked list of (item,
count)" feature (architecture plan §4/§5) — one implementation of the
tie-break subtlety (see below), reused by every query source, rather than
copies that could silently drift. Two shapes use it:

  - paginate_sql_source: for a source subquery, runs type_count,
    result_count and the page as three statements (word frequency, error
    frequency, and Tier 0's self-join).
  - match_condition / order_clauses directly: for a builder that folds all
    three into ONE statement (_window_ngram_query.py, where recomputing the
    source three times would triple the cost of the most expensive feature).

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


def match_condition(column, query: str | None, match_mode: MatchMode):
    """The `q` filter as a WHERE predicate on `column`, or None when there is
    no filter. The term is stripped and lowercased; callers whose items keep
    their original casing pass `func.lower(item)` as `column`."""
    if not query:
        return None

    term = query.strip().lower()
    if match_mode == MatchMode.STARTS:
        return column.startswith(term)
    if match_mode == MatchMode.ENDS:
        return column.endswith(term)
    if match_mode == MatchMode.EXACT:
        return column == term
    return column.contains(term)


def order_clauses(item_column, count_column, sort: str, direction: SortDirection) -> list:
    """ORDER BY terms implementing the tie-break rule in this module's
    docstring."""
    if sort == "item":
        return [item_column.asc() if direction == SortDirection.ASC else item_column.desc()]

    count_order = count_column.asc() if direction == SortDirection.ASC else count_column.desc()
    return [count_order, item_column.asc()]


async def paginate_sql_source(
    session: AsyncSession,
    source,  # a SQLAlchemy Subquery exposing .c.item (text) and .c.count (int)
    query: str | None,
    match_mode: MatchMode,
    sort: str,
    direction: SortDirection,
    limit: int,
    offset: int,
    case_sensitive: bool = True,
) -> tuple[list[RankedRow], int, int]:
    """For sources where `item` is a real column the database can filter on
    directly (Tier 0's self-join CONCAT, Tier 2's word-frequency JOIN to
    WordType.form). Returns (page_rows, result_count, type_count).

    `case_sensitive=False` (docs/feature-list.md, Error Frequency)
    matches the lowercased `query` term against `func.lower(item)` instead
    of `item` directly, while still SELECTing/returning `item`'s original
    casing — for a source whose item text preserves case on purpose (an
    erroneous phrase's original casing is signal, not noise, for codes like
    `ejk`). Every existing caller (word frequency/collocations/n-grams)
    already stores `item` pre-lowercased by the tokenizer, so the default
    leaves their behavior byte-for-byte unchanged."""
    type_count = (await session.execute(select(func.count()).select_from(source))).scalar_one()

    filtered = select(source.c.item, source.c.count)
    condition = match_condition(source.c.item if case_sensitive else func.lower(source.c.item), query, match_mode)
    if condition is not None:
        filtered = filtered.where(condition)

    result_count = (await session.execute(select(func.count()).select_from(filtered.subquery()))).scalar_one()

    filtered = filtered.order_by(*order_clauses(source.c.item, source.c.count, sort, direction))
    page_rows = (await session.execute(filtered.limit(limit).offset(offset))).all()

    return [RankedRow(item=item, count=count) for item, count in page_rows], result_count, type_count
