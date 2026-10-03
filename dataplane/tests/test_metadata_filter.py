"""The secondary metadata filter at its single chokepoint,
repositories/shared.py::resolve_document_ids (docs/metadata-catalogue-plan.md).

The case worth the real database: a facet's "(no value)" option must select
BOTH a document whose catalogue row leaves the field empty AND a document with
no catalogue row at all. Those are one SQL expression only because the join is
LEFT OUTER — an inner join, or a NULL test written against the wrong side,
passes a hand-written unit test and silently drops rows here.

Seeds its own catalogue rows against the shared `seeded_corpus` documents
rather than adding them to conftest: every other feature's tests assert exact
token counts over that corpus, and metadata rows must stay invisible to them.
"""

import pytest

from dataplane.repositories.base import MetadataFilter
from dataplane.repositories.shared import resolve_document_ids


@pytest.fixture
def catalogued(seeded_corpus):
    """doc1 fully described; doc2 described but with every facet empty; the
    error-corpus document left out of the catalogue entirely."""
    from main.models import DocumentMetadata

    DocumentMetadata.objects.all().delete()

    DocumentMetadata.objects.create(
        source_filename="annotated.txt",
        match_key="annotated",
        document_id=seeded_corpus["doc1_id"],
        university="TUFS",
        year=2023,
        grade=3,
        topic="wawancara",
        topic_en="interview",
        word_count=650,
        name_code="KOMSHI",
        source_file="test.csv",
    )
    DocumentMetadata.objects.create(
        source_filename="plain.txt",
        match_key="plain",
        document_id=seeded_corpus["doc2_id"],
        university=None,
        year=None,
        grade=None,
        source_file="test.csv",
    )
    # An entry for a file nobody has uploaded — must never appear in a result,
    # and must not make the facet endpoint offer its value.
    DocumentMetadata.objects.create(
        source_filename="never-uploaded.txt",
        match_key="never-uploaded",
        document=None,
        university="SFC",
        year=2024,
        grade=1,
        source_file="test.csv",
    )

    yield seeded_corpus

    DocumentMetadata.objects.all().delete()


async def _ids(session, catalogued, **kwargs):
    return set(
        await resolve_document_ids(
            session, [catalogued["corpus_id"]], MetadataFilter(**kwargs)
        )
    )


async def test_no_filter_returns_the_whole_corpus(session, catalogued):
    both = {catalogued["doc1_id"], catalogued["doc2_id"]}
    assert await _ids(session, catalogued) == both
    # None, not just an empty filter, is the default every existing caller uses.
    assert set(await resolve_document_ids(session, [catalogued["corpus_id"]])) == both


async def test_concrete_value_selects_only_that_document(session, catalogued):
    assert await _ids(session, catalogued, universities=("TUFS",)) == {catalogued["doc1_id"]}


async def test_unset_selects_both_the_empty_field_and_the_absent_row(session, catalogued):
    """The load-bearing case. doc2 has a catalogue row whose university is
    NULL; the error-corpus doc has no row at all. Both read as "no value" to
    anyone looking at results, so both must come back."""
    assert await _ids(session, catalogued, unset=frozenset({"university"})) == {
        catalogued["doc2_id"]
    }

    # Widen the corpus selection to include the uncatalogued document and it
    # must come back too, from the same single predicate.
    ids = set(
        await resolve_document_ids(
            session,
            [catalogued["corpus_id"], catalogued["error_corpus_id"]],
            MetadataFilter(unset=frozenset({"university"})),
        )
    )
    assert ids == {catalogued["doc2_id"], catalogued["error_doc_id"]}


async def test_value_or_unset_within_one_facet_is_a_union(session, catalogued):
    assert await _ids(
        session, catalogued, universities=("TUFS",), unset=frozenset({"university"})
    ) == {catalogued["doc1_id"], catalogued["doc2_id"]}


async def test_facets_combine_with_and(session, catalogued):
    assert await _ids(session, catalogued, universities=("TUFS",), grades=(3,)) == {
        catalogued["doc1_id"]
    }
    # doc1 is TUFS but grade 3, so an AND against grade 1 must be empty rather
    # than falling back to the university match.
    assert await _ids(session, catalogued, universities=("TUFS",), grades=(1,)) == set()


async def test_numeric_facets_filter_and_their_unset_works(session, catalogued):
    assert await _ids(session, catalogued, years=(2023,)) == {catalogued["doc1_id"]}
    assert await _ids(session, catalogued, grades=(3,)) == {catalogued["doc1_id"]}
    assert await _ids(session, catalogued, unset=frozenset({"grade"})) == {
        catalogued["doc2_id"]
    }


async def test_unmatched_filter_returns_empty_not_everything(session, catalogued):
    """An empty list is the established "nothing to compute" signal that every
    query builder already guards on — so this is the whole short-circuit."""
    assert await _ids(session, catalogued, universities=("NOPE",)) == set()


async def test_name_code_matches_on_prefix_case_insensitively(session, catalogued):
    assert await _ids(session, catalogued, name_code="KOM") == {catalogued["doc1_id"]}
    assert await _ids(session, catalogued, name_code="kom") == {catalogued["doc1_id"]}
    assert await _ids(session, catalogued, name_code="OMSHI") == set()  # prefix, not substring


async def test_name_code_wildcards_are_escaped_not_interpreted(session, catalogued):
    """A literal % must not turn into "match everything"."""
    assert await _ids(session, catalogued, name_code="%") == set()
    assert await _ids(session, catalogued, name_code="K_M") == set()


async def test_entry_without_a_document_never_leaks_into_results(session, catalogued):
    """never-uploaded.txt is SFC/2024/grade 1 and must be unreachable."""
    assert await _ids(session, catalogued, universities=("SFC",)) == set()
    assert await _ids(session, catalogued, years=(2024,)) == set()


async def test_empty_corpus_selection_still_short_circuits(session, catalogued):
    assert await resolve_document_ids(session, [], MetadataFilter(universities=("TUFS",))) == []
