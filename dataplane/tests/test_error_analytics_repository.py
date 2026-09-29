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

    async def test_or_across_error_codes(self, seeded_corpus, session):
        repo = PostgresErrorAnalyticsRepository(session)
        page = await repo.frequency(
            [seeded_corpus["error_doc_id"]],
            ErrorFilter(error_codes=["ejk"]),
            None, MatchMode.CONTAINS, "count", SortDirection.DESC, 50, 0,
        )

        by_phrase = {row.phrase: row.frequency for row in page.rows}
        assert by_phrase == {"nasi": 1, "Sayang": 1}  # both ejk occurrences, urtfn excluded

    async def test_and_across_category_paths_narrows_to_the_intersection(self, seeded_corpus, session):
        repo = PostgresErrorAnalyticsRepository(session)
        page = await repo.frequency(
            [seeded_corpus["error_doc_id"]],
            ErrorFilter(category_paths=["gramatikal", "frasa-nomina"]),
            None, MatchMode.CONTAINS, "count", SortDirection.DESC, 50, 0,
        )

        assert [row.phrase for row in page.rows] == ["goreng nasi"]

    async def test_and_across_category_paths_from_different_branches_yields_nothing(self, seeded_corpus, session):
        # "gramatikal" and "ejaan" are sibling top categories — no leaf can
        # be under both, so this must return empty, not an error.
        repo = PostgresErrorAnalyticsRepository(session)
        page = await repo.frequency(
            [seeded_corpus["error_doc_id"]],
            ErrorFilter(category_paths=["gramatikal", "ejaan"]),
            None, MatchMode.CONTAINS, "count", SortDirection.DESC, 50, 0,
        )

        assert page.rows == []
        assert page.result_count == 0

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
            [seeded_corpus["error_doc_id"]], ErrorFilter(error_codes=["urtfn"]), window=2, limit=50, offset=0,
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
