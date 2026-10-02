"""
Integration tests for PostgresErrorAnalyticsRepository, run against a real
Postgres database (same reasoning as test_kwic_repository.py). Exercises
against ERROR_CORPUS_XML (conftest.py), whose annotations once indexed are:
  id=1 -> ejk,   span [3,4) "nasi"        (nested inside id=3)
  id=3 -> urtfn, span [2,4) "goreng nasi", correction "nasi goreng"
  id=4 -> unresolved ("eror;doesnotexist"), span [5,6) "lagi"
  id=5 -> ejk,   span [6,7) "Sayang"

seeded_corpus/session fixtures live in conftest.py; `error_corpus_id` /
`error_doc_id` are the extra keys that fixture adds for this document.
"""

from dataplane.repositories.base import ErrorFilter, MatchMode, SortDirection
from dataplane.repositories.postgres.error_analytics_repository import PostgresErrorAnalyticsRepository


class TestFrequency:
    async def test_no_filter_excludes_unresolved_annotations(self, seeded_corpus, session):
        repo = PostgresErrorAnalyticsRepository(session)
        page = await repo.frequency(
            [seeded_corpus["error_doc_id"]], ErrorFilter(), None, MatchMode.CONTAINS, "count", SortDirection.DESC, 50, 0,
        )

        phrases = {row.phrase for row in page.rows}
        assert phrases == {"nasi", "goreng nasi", "Sayang"}  # never "lagi" — its features don't resolve

    async def test_or_across_leaf_codes(self, seeded_corpus, session):
        repo = PostgresErrorAnalyticsRepository(session)
        page = await repo.frequency(
            [seeded_corpus["error_doc_id"]],
            ErrorFilter(codes=["ejk"]),
            None, MatchMode.CONTAINS, "count", SortDirection.DESC, 50, 0,
        )

        by_phrase = {row.phrase: row.frequency for row in page.rows}
        assert by_phrase == {"nasi": 1, "Sayang": 1}  # both ejk occurrences, urtfn excluded

    async def test_category_code_cascades_to_every_leaf_descendant(self, seeded_corpus, session):
        # A tree-picker "select this category" click sends just the
        # category's own code — checking "gramatikal" must resolve to
        # exactly the same leaves as explicitly listing every leaf under
        # it (here, just urtfn), same mechanism as a leaf code, no
        # separate exact-match branch.
        repo = PostgresErrorAnalyticsRepository(session)
        page = await repo.frequency(
            [seeded_corpus["error_doc_id"]],
            ErrorFilter(codes=["gramatikal"]),
            None, MatchMode.CONTAINS, "count", SortDirection.DESC, 50, 0,
        )

        assert [row.phrase for row in page.rows] == ["goreng nasi"]

    async def test_multiple_codes_union_across_branches(self, seeded_corpus, session):
        # Checking two different branches (or a leaf plus an unrelated
        # category) unions their results — the tree-picker's "checking
        # several nodes means either" semantics, replacing the earlier
        # AND-drill-down design (checking two sibling categories used to
        # mean "under both", which is never satisfiable for unrelated
        # branches; a real tree checkbox doesn't work that way).
        repo = PostgresErrorAnalyticsRepository(session)
        page = await repo.frequency(
            [seeded_corpus["error_doc_id"]],
            ErrorFilter(codes=["gramatikal", "ejaan"]),
            None, MatchMode.CONTAINS, "count", SortDirection.DESC, 50, 0,
        )

        assert {row.phrase for row in page.rows} == {"nasi", "goreng nasi", "Sayang"}

    async def test_leaf_and_category_codes_can_be_mixed(self, seeded_corpus, session):
        repo = PostgresErrorAnalyticsRepository(session)
        page = await repo.frequency(
            [seeded_corpus["error_doc_id"]],
            ErrorFilter(codes=["ejk", "gramatikal"]),
            None, MatchMode.CONTAINS, "count", SortDirection.DESC, 50, 0,
        )

        assert {row.phrase for row in page.rows} == {"nasi", "goreng nasi", "Sayang"}

    async def test_query_filter_matches_case_insensitively_but_preserves_display_case(self, seeded_corpus, session):
        repo = PostgresErrorAnalyticsRepository(session)
        page = await repo.frequency(
            [seeded_corpus["error_doc_id"]], ErrorFilter(), "sayang", MatchMode.CONTAINS, "count", SortDirection.DESC, 50, 0,
        )

        assert [row.phrase for row in page.rows] == ["Sayang"]  # matched via lowercase "sayang", displayed with its real casing


class TestOccurrences:
    async def test_occurrences_excludes_unresolved_annotations(self, seeded_corpus, session):
        repo = PostgresErrorAnalyticsRepository(session)
        hits = await repo.occurrences([seeded_corpus["error_doc_id"]], ErrorFilter(), window=2, limit=50, offset=0)

        assert len(hits) == 3
        assert all(hit.error_code != "" for hit in hits)
        codes = sorted(hit.error_code for hit in hits)
        assert codes == ["ejk", "ejk", "urtfn"]

    async def test_occurrence_carries_context_and_correction(self, seeded_corpus, session):
        repo = PostgresErrorAnalyticsRepository(session)
        hits = await repo.occurrences(
            [seeded_corpus["error_doc_id"]], ErrorFilter(codes=["urtfn"]), window=2, limit=50, offset=0,
        )

        assert len(hits) == 1
        hit = hits[0]
        assert hit.keyword == ["goreng", "nasi"]
        assert hit.left == ["saya", "suka"]
        assert hit.right == ["sekali", "lagi"]
        assert hit.error_code == "urtfn"
        assert hit.category_path == "eror;gramatikal;frasa-nomina;urtfn"
        assert hit.correction_text == "nasi goreng"

    async def test_count_occurrences_agrees_with_occurrences_length_when_unpaginated(self, seeded_corpus, session):
        repo = PostgresErrorAnalyticsRepository(session)
        total = await repo.count_occurrences([seeded_corpus["error_doc_id"]], ErrorFilter())
        hits = await repo.occurrences([seeded_corpus["error_doc_id"]], ErrorFilter(), window=2, limit=50, offset=0)
        assert total == len(hits) == 3


class TestSummary:
    async def test_summary_groups_by_top_category_and_excludes_unresolved(self, seeded_corpus, session):
        repo = PostgresErrorAnalyticsRepository(session)
        summary = await repo.summary([seeded_corpus["error_doc_id"]])

        by_category = {c.category: c.count for c in summary.by_category}
        assert by_category == {"ejaan": 2, "gramatikal": 1}
        assert summary.total == 3

    async def test_by_node_rolls_descendant_counts_up_to_every_ancestor(self, seeded_corpus, session):
        repo = PostgresErrorAnalyticsRepository(session)
        summary = await repo.summary([seeded_corpus["error_doc_id"]])

        counts = {n.code: n.count for n in summary.by_node}
        # ejk x2 at eror;ejaan;ejk and urtfn x1 at
        # eror;gramatikal;frasa-nomina;urtfn — every intermediate node on
        # both paths carries its subtree's total, at any depth.
        assert counts == {"ejaan": 2, "ejk": 2, "gramatikal": 1, "frasa-nomina": 1, "urtfn": 1}

    async def test_by_node_separates_subtree_count_from_self_count(self, seeded_corpus, session):
        repo = PostgresErrorAnalyticsRepository(session)
        summary = await repo.summary([seeded_corpus["error_doc_id"]])

        self_counts = {n.code: n.self_count for n in summary.by_node}
        # Only the leaves the annotations actually resolved to count as
        # "self" — an ancestor's own tally stays 0 while its subtree is 2.
        assert self_counts == {"ejaan": 0, "ejk": 2, "gramatikal": 0, "frasa-nomina": 0, "urtfn": 1}

    async def test_by_node_excludes_the_literal_root_and_carries_full_paths(self, seeded_corpus, session):
        repo = PostgresErrorAnalyticsRepository(session)
        summary = await repo.summary([seeded_corpus["error_doc_id"]])

        assert "eror" not in {n.code for n in summary.by_node}
        paths = {n.code: n.path for n in summary.by_node}
        assert paths["gramatikal"] == "eror;gramatikal"
        assert paths["urtfn"] == "eror;gramatikal;frasa-nomina;urtfn"

    async def test_by_node_top_level_agrees_with_by_category_and_total(self, seeded_corpus, session):
        repo = PostgresErrorAnalyticsRepository(session)
        summary = await repo.summary([seeded_corpus["error_doc_id"]])

        top_level = {n.code: n.count for n in summary.by_node if n.path.count(";") == 1}
        assert top_level == {c.category: c.count for c in summary.by_category}
        assert sum(top_level.values()) == summary.total

    async def test_summary_of_no_documents_is_empty(self, session):
        repo = PostgresErrorAnalyticsRepository(session)
        summary = await repo.summary([])

        assert summary.total == 0
        assert summary.by_category == []
        assert summary.by_node == []
