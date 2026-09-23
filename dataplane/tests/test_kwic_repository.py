"""
Integration tests for PostgresKWICRepository, run against a REAL Postgres
database — asyncpg has no sqlite equivalent, and the self-join query this
repository builds is exactly the kind of thing that only fails at the SQL
level (see the ordering bug this test style caught during development:
`token.c.document_id` referenced outside its own FROM clause, invisible
without actually executing the query against Postgres).

seeded_corpus / session / document_ids fixtures live in conftest.py (shared
with test_ranked_query.py and the Phase 4 router tests, once a third file
needed the same trio this file originally defined alone).
"""

from dataplane.repositories.base import Mode
from dataplane.repositories.postgres.kwic_repository import PostgresKWICRepository


class TestSingleWordSearch:
    async def test_finds_every_occurrence_across_documents(self, session, document_ids):
        repo = PostgresKWICRepository(session)
        hits = await repo.search(document_ids, ["nasi"], Mode.ORIGINAL, window=2, limit=50, offset=0)

        assert len(hits) == 4
        assert all(hit.keyword == ["nasi"] for hit in hits)

    async def test_count_matches_agrees_with_search_length_when_unpaginated(self, session, document_ids):
        repo = PostgresKWICRepository(session)
        total = await repo.count_matches(document_ids, ["nasi"], Mode.ORIGINAL)
        hits = await repo.search(document_ids, ["nasi"], Mode.ORIGINAL, window=2, limit=50, offset=0)
        assert total == len(hits) == 4

    async def test_context_window_matches_expected_neighbors(self, seeded_corpus, session, document_ids):
        repo = PostgresKWICRepository(session)
        hits = await repo.search(document_ids, ["nasi"], Mode.ORIGINAL, window=2, limit=50, offset=0)

        doc1_hits = [h for h in hits if h.document_id == seeded_corpus["doc1_id"]]
        assert {(" ".join(h.left), " ".join(h.right)) for h in doc1_hits} == {
            ("suka makan", "goreng tapi"),
            ("tidak suka", "goreng lagi"),
        }

    async def test_window_truncates_gracefully_at_document_boundary(self, seeded_corpus, session, document_ids):
        # "nasi" at position 3 in doc1 (0-indexed) has only 3 words before it
        # — window=10 must not error or pad, just return what exists.
        repo = PostgresKWICRepository(session)
        hits = await repo.search(document_ids, ["nasi"], Mode.ORIGINAL, window=10, limit=50, offset=0)
        first_hit = min((h for h in hits if h.document_id == seeded_corpus["doc1_id"]), key=lambda h: len(h.left))
        assert first_hit.left == ["saya", "suka", "makan"]

    async def test_pagination_splits_results_without_gaps_or_overlap(self, session, document_ids):
        repo = PostgresKWICRepository(session)
        page1 = await repo.search(document_ids, ["nasi"], Mode.ORIGINAL, window=1, limit=2, offset=0)
        page2 = await repo.search(document_ids, ["nasi"], Mode.ORIGINAL, window=1, limit=2, offset=2)
        full = await repo.search(document_ids, ["nasi"], Mode.ORIGINAL, window=1, limit=50, offset=0)

        assert page1 + page2 == full

    async def test_no_matches_returns_empty_not_an_error(self, session, document_ids):
        repo = PostgresKWICRepository(session)
        hits = await repo.search(document_ids, ["xyznonexistent"], Mode.ORIGINAL, window=2, limit=50, offset=0)
        assert hits == []
        assert await repo.count_matches(document_ids, ["xyznonexistent"], Mode.ORIGINAL) == 0


class TestPhraseSearch:
    """Proves the KWICRepository interface's `words: list[str]` can absorb
    legacy `kwic`'s multi-word phrase search (architecture plan §4) — not
    just single-word lookups."""

    async def test_two_word_phrase_finds_contiguous_occurrences_only(self, session, document_ids):
        repo = PostgresKWICRepository(session)
        hits = await repo.search(document_ids, ["nasi", "goreng"], Mode.ORIGINAL, window=3, limit=50, offset=0)

        assert len(hits) == 4
        assert all(hit.keyword == ["nasi", "goreng"] for hit in hits)

    async def test_phrase_context_excludes_the_matched_words_themselves(self, seeded_corpus, session, document_ids):
        repo = PostgresKWICRepository(session)
        hits = await repo.search(document_ids, ["nasi", "goreng"], Mode.ORIGINAL, window=3, limit=50, offset=0)
        doc1_hit = next(h for h in hits if h.document_id == seeded_corpus["doc1_id"] and h.left == ["saya", "suka", "makan"])

        assert doc1_hit.right == ["tapi", "saya", "tidak"]
        assert "nasi" not in doc1_hit.left and "goreng" not in doc1_hit.right


class TestCorrectedMode:
    """Mirrors main/corpus_parsing.py's segment handling: a <segment>'s
    inner text feeds the original stream, its Correction attribute feeds
    the corrected stream — the two modes must never cross-contaminate."""

    async def test_original_word_absent_from_corrected_stream(self, session, document_ids):
        repo = PostgresKWICRepository(session)
        hits = await repo.search(document_ids, ["tapi"], Mode.CORRECTED, window=3, limit=50, offset=0)
        assert hits == []

    async def test_correction_word_absent_from_original_stream(self, session, document_ids):
        repo = PostgresKWICRepository(session)
        hits = await repo.search(document_ids, ["tetapi"], Mode.ORIGINAL, window=3, limit=50, offset=0)
        assert hits == []

    async def test_correction_word_present_in_corrected_stream(self, seeded_corpus, session, document_ids):
        repo = PostgresKWICRepository(session)
        hits = await repo.search(document_ids, ["tetapi"], Mode.CORRECTED, window=3, limit=50, offset=0)
        assert len(hits) == 1
        assert hits[0].document_id == seeded_corpus["doc1_id"]
        assert hits[0].left == ["makan", "nasi", "goreng"]
        assert hits[0].right == ["saya", "tidak", "suka"]
