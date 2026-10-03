"""
Integration tests for compute_ranked_page (dataplane/repositories/postgres/
_ranked_query.py) — the shared query behind FrequencyRepository,
CollocationRepository, and NgramRepository. Run against a REAL Postgres
database, same reasoning and same seeded_corpus fixture as
test_kwic_repository.py: self-joins and GROUP BY semantics are exactly the
kind of thing that only fail at the SQL level.

All expected values below were hand-verified against the seeded fixture's
known token sequence before being written as assertions (see
docs/new-architecture.md's Phase 4 status entry) — not derived from running
this code and trusting the output. seeded_corpus / session / document_ids
fixtures live in conftest.py.
"""

from dataplane.repositories.base import MatchMode, Mode, RankedPage, SortDirection
from dataplane.repositories.postgres._ranked_query import compute_ranked_page


class TestWordFrequency:
    """n=1 — degenerates to a plain GROUP BY word, no self-join."""

    async def test_counts_every_word_across_both_documents(self, session, document_ids):
        page = await compute_ranked_page(
            session, document_ids, Mode.ORIGINAL, 1, None, MatchMode.CONTAINS, "count", SortDirection.DESC, 50, 0
        )

        by_item = {row.item: row.count for row in page.rows}
        assert by_item == {
            "goreng": 4,
            "nasi": 4,
            "saya": 4,
            "suka": 4,
            "lagi": 2,
            "dan": 1,
            "makan": 1,
            "tapi": 1,
            "tidak": 1,
        }
        assert page.type_count == 9
        assert page.result_count == 9
        assert page.token_total == 22  # 12-token doc1 + 10-token doc2

    async def test_default_sort_is_count_desc_with_alphabetical_tie_break(self, session, document_ids):
        # The subtle case: Python's stable sort keeps ties in their
        # PRIOR (alphabetical) order regardless of reverse=True/False — a
        # naive "ORDER BY count DESC, item DESC" would reverse the tied
        # group's alphabetical order and silently diverge from Django.
        page = await compute_ranked_page(
            session, document_ids, Mode.ORIGINAL, 1, None, MatchMode.CONTAINS, "count", SortDirection.DESC, 50, 0
        )
        items = [row.item for row in page.rows]
        # count=4 group, tied, must stay alphabetically ascending:
        assert items[:4] == ["goreng", "nasi", "saya", "suka"]

    async def test_sort_count_asc_still_ties_alphabetically_ascending(self, session, document_ids):
        page = await compute_ranked_page(
            session, document_ids, Mode.ORIGINAL, 1, None, MatchMode.CONTAINS, "count", SortDirection.ASC, 50, 0
        )
        items = [row.item for row in page.rows]
        # count=1 group comes first ascending, still alphabetical:
        assert items[:4] == ["dan", "makan", "tapi", "tidak"]

    async def test_filter_starts_with(self, session, document_ids):
        page = await compute_ranked_page(
            session, document_ids, Mode.ORIGINAL, 1, "nasi", MatchMode.STARTS, "count", SortDirection.DESC, 50, 0
        )
        assert [row.item for row in page.rows] == ["nasi"]
        assert page.result_count == 1
        # type_count is UNFILTERED — still every distinct word:
        assert page.type_count == 9

    async def test_corrected_mode_substitutes_the_correction(self, session, document_ids):
        page = await compute_ranked_page(
            session, document_ids, Mode.CORRECTED, 1, None, MatchMode.CONTAINS, "count", SortDirection.DESC, 50, 0
        )
        by_item = {row.item: row.count for row in page.rows}
        assert "tapi" not in by_item
        assert by_item["tetapi"] == 1

    async def test_pagination(self, session, document_ids):
        page1 = await compute_ranked_page(
            session, document_ids, Mode.ORIGINAL, 1, None, MatchMode.CONTAINS, "item", SortDirection.ASC, 2, 0
        )
        page2 = await compute_ranked_page(
            session, document_ids, Mode.ORIGINAL, 1, None, MatchMode.CONTAINS, "item", SortDirection.ASC, 2, 2
        )
        full = await compute_ranked_page(
            session, document_ids, Mode.ORIGINAL, 1, None, MatchMode.CONTAINS, "item", SortDirection.ASC, 50, 0
        )
        assert page1.rows + page2.rows == full.rows[:4]

    async def test_empty_document_ids_returns_empty_page_not_an_error(self, session):
        page = await compute_ranked_page(
            session, [], Mode.ORIGINAL, 1, None, MatchMode.CONTAINS, "count", SortDirection.DESC, 50, 0
        )
        assert page == RankedPage(rows=[], result_count=0, type_count=0, token_total=0)


class TestCollocations:
    """n=2 — adjacent word pairs within a document, never across documents."""

    async def test_counts_adjacent_pairs(self, session, document_ids):
        page = await compute_ranked_page(
            session, document_ids, Mode.ORIGINAL, 2, None, MatchMode.CONTAINS, "count", SortDirection.DESC, 50, 0
        )
        by_item = {row.item: row.count for row in page.rows}

        assert by_item["nasi goreng"] == 4  # 2 occurrences per document x 2 documents
        assert by_item["saya suka"] == 3
        assert by_item["suka nasi"] == 3
        assert page.type_count == 12
        # token_total stays the WORD count, not the pair count (11+9=20 pairs != 22 words):
        assert page.token_total == 22

    async def test_pairs_never_cross_a_document_boundary(self, session, document_ids):
        # doc1 ends "... goreng lagi" and doc2 starts "saya suka ..." — if
        # documents were flattened together, "lagi saya" would appear as a
        # spurious pair. It must not.
        page = await compute_ranked_page(
            session, document_ids, Mode.ORIGINAL, 2, None, MatchMode.CONTAINS, "count", SortDirection.DESC, 250, 0
        )
        items = {row.item for row in page.rows}
        assert "lagi saya" not in items


class TestNgrams:
    """n=3-5 — same shape, more self-join hops."""

    async def test_trigram_counts(self, session, document_ids):
        page = await compute_ranked_page(
            session, document_ids, Mode.ORIGINAL, 3, None, MatchMode.CONTAINS, "count", SortDirection.DESC, 50, 0
        )
        by_item = {row.item: row.count for row in page.rows}

        assert by_item["suka nasi goreng"] == 3
        assert by_item["nasi goreng lagi"] == 2
        assert by_item["saya suka nasi"] == 2
        assert page.type_count == 14

    async def test_larger_n_still_correct(self, session, document_ids):
        page = await compute_ranked_page(
            session, document_ids, Mode.ORIGINAL, 5, None, MatchMode.CONTAINS, "count", SortDirection.DESC, 250, 0
        )
        # Every 5-gram window's item string must be exactly 5 words — a
        # self-join fan-out bug would silently inflate this.
        assert all(len(row.item.split(" ")) == 5 for row in page.rows)
        assert page.result_count == len(page.rows)
