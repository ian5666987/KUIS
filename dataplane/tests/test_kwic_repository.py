"""
Integration tests for PostgresKWICRepository, run against a REAL Postgres
database — asyncpg has no sqlite equivalent, and the self-join query this
repository builds is exactly the kind of thing that only fails at the SQL
level (see the ordering bug this test style caught during development:
`token.c.document_id` referenced outside its own FROM clause, invisible
without actually executing the query against Postgres).

Safety guard: refuses to run unless DB_NAME contains "test" — this fixture
DELETES ALL Document/Corpus rows in whatever database it's pointed at
before seeding known fixture data, so accidentally pointing it at a real
dev/prod database would be destructive. Point DB_NAME at a dedicated,
disposable test database (locally: `createdb kuis_dataplane_test`; in CI:
the ephemeral `postgres` service container — see .github/workflows/ci.yml).
"""

import os

import django
import pytest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.core.management import call_command  # noqa: E402

from dataplane.core.db import async_session_factory, engine  # noqa: E402
from dataplane.repositories.base import Mode  # noqa: E402
from dataplane.repositories.postgres.kwic_repository import PostgresKWICRepository  # noqa: E402
from dataplane.repositories.shared import resolve_document_ids  # noqa: E402

CORPUS_XML = """<document>
  <header><textfile>sample</textfile><lang>indonesian</lang></header>
  <body>
    saya suka makan nasi goreng
    <segment id="1" features="eror;leksikal" Correction="tetapi">tapi</segment>
    saya tidak suka nasi goreng lagi
  </body>
</document>"""

PLAIN_TEXT = "saya suka nasi goreng dan saya suka nasi goreng lagi"


@pytest.fixture(scope="module")
def seeded_corpus():
    db_name = os.environ.get("DB_NAME", "")
    if "test" not in db_name.lower():
        pytest.fail(
            f"Refusing to run: DB_NAME={db_name!r} doesn't look like a test database "
            "(must contain 'test'). This fixture deletes all Document/Corpus rows."
        )

    call_command("migrate", "--noinput", verbosity=0)

    from main.models import Corpus, Document

    Document.objects.all().delete()
    Corpus.objects.all().delete()

    annotated = Document.objects.create(title="annotated.xml", content=CORPUS_XML)
    plain = Document.objects.create(title="plain.txt", content=PLAIN_TEXT)
    corpus = Corpus.objects.create(name="Test corpus")
    corpus.documents.set([annotated, plain])

    return {"corpus_id": corpus.id, "doc1_id": annotated.id, "doc2_id": plain.id}


@pytest.fixture
async def session():
    async with async_session_factory() as s:
        yield s
    await engine.dispose()


@pytest.fixture
async def document_ids(seeded_corpus, session):
    return await resolve_document_ids(session, [seeded_corpus["corpus_id"]])


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
