"""Endpoint-level tests for the secondary metadata filter
(docs/data-processing.md): GET /api/v1/metadata-facets, and the filter
params riding along on the analysis endpoints.

The point of testing it at the router level as well as at
resolve_document_ids: the params are declared once as a Depends()-able object
shared by seven endpoints, so what needs proving here is that FastAPI actually
binds them as query params on each endpoint — and that the `__none__` sentinel
survives the string -> int coercion that `year`/`grade` need.
"""

import pytest

from dataplane.repositories.base import METADATA_UNSET


@pytest.fixture
def catalogued(seeded_corpus):
    from main.models import DocumentMetadata

    DocumentMetadata.objects.all().delete()
    DocumentMetadata.objects.create(
        source_filename="annotated.txt", match_key="annotated",
        document_id=seeded_corpus["doc1_id"],
        university="TUFS", year=2023, grade=3, name_code="KOMSHI", source_file="test.csv",
    )
    DocumentMetadata.objects.create(
        source_filename="plain.txt", match_key="plain",
        document_id=seeded_corpus["doc2_id"], source_file="test.csv",
    )
    DocumentMetadata.objects.create(
        source_filename="never-uploaded.txt", match_key="never-uploaded",
        document=None, university="SFC", year=2024, grade=1, source_file="test.csv",
    )
    yield seeded_corpus
    DocumentMetadata.objects.all().delete()


# --- facets ---------------------------------------------------------------


def test_facets_requires_authentication(client, catalogued):
    assert client.get("/api/v1/metadata-facets").status_code == 401


def test_facets_only_offers_values_that_have_a_linked_document(client, auth_headers, catalogued):
    """SFC/2024/grade 1 exist in the catalogue but only on an entry with no
    uploaded file — offering them would hand the user a dead-end filter."""
    body = client.get("/api/v1/metadata-facets", headers=auth_headers).json()

    assert body["universities"] == ["TUFS"]
    assert body["years"] == [2023]
    assert body["grades"] == [3]
    # Served rather than hardcoded client-side, so the sentinel has one
    # definition the frontend can see.
    assert body["unset_value"] == METADATA_UNSET


# --- the filter on the analysis endpoints ---------------------------------

# Every endpoint that takes corpus_ids, with the params it additionally needs.
ENDPOINTS = [
    ("/api/v1/frequency", {}),
    ("/api/v1/collocations", {}),
    ("/api/v1/ngrams", {"n": "2"}),
    ("/api/v1/kwic", {"q": "saya"}),
    ("/api/v1/error-frequency", {}),
    ("/api/v1/epic", {}),
    ("/api/v1/error-summary", {}),
]


@pytest.mark.parametrize("path,extra", ENDPOINTS)
def test_every_endpoint_accepts_the_filter_params(client, auth_headers, catalogued, path, extra):
    response = client.get(
        path,
        params={
            "corpus_ids": catalogued["corpus_id"],
            "university": "TUFS",
            "year": "2023",
            "grade": "3",
            "name_code": "KOM",
            **extra,
        },
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("path,extra", ENDPOINTS)
def test_unset_sentinel_is_accepted_on_every_numeric_facet(client, auth_headers, catalogued, path, extra):
    """`year`/`grade` are declared as strings precisely so the sentinel can
    travel in the facet's own param. A 422 here means that broke."""
    response = client.get(
        path,
        params={
            "corpus_ids": catalogued["corpus_id"],
            "year": METADATA_UNSET,
            "grade": METADATA_UNSET,
            "university": METADATA_UNSET,
            **extra,
        },
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text


def test_filter_actually_narrows_the_result(client, auth_headers, catalogued):
    """Both documents share the word "saya"; only doc1 is TUFS. The filtered
    token_total must drop, not just the row set."""
    def frequency(**params):
        return client.get(
            "/api/v1/frequency",
            params={"corpus_ids": catalogued["corpus_id"], "per_page": 100, **params},
            headers=auth_headers,
        ).json()

    unfiltered = frequency()
    filtered = frequency(university="TUFS")

    assert filtered["token_total"] < unfiltered["token_total"]
    assert filtered["token_total"] > 0


def test_unset_filter_selects_the_document_with_no_values(client, auth_headers, catalogued):
    def total(**params):
        return client.get(
            "/api/v1/frequency",
            params={"corpus_ids": catalogued["corpus_id"], **params},
            headers=auth_headers,
        ).json()["token_total"]

    unfiltered = total()
    tufs = total(university="TUFS")
    unset = total(university=METADATA_UNSET)

    # The two facet selections partition the corpus: one document has a
    # university, the other has none, and nothing is counted twice.
    assert tufs + unset == unfiltered


def test_filter_matching_nothing_yields_an_empty_result_not_everything(client, auth_headers, catalogued):
    body = client.get(
        "/api/v1/frequency",
        params={"corpus_ids": catalogued["corpus_id"], "university": "NOPE"},
        headers=auth_headers,
    ).json()

    assert body["results"] == []
    assert body["result_count"] == 0
    assert body["token_total"] == 0


def test_junk_on_a_numeric_facet_widens_rather_than_erroring(client, auth_headers, catalogued):
    """A stale or hand-edited URL should degrade to a wider result, never a
    422 — the filter is a navigation control."""
    body = client.get(
        "/api/v1/frequency",
        params={"corpus_ids": catalogued["corpus_id"], "grade": "not-a-number"},
        headers=auth_headers,
    )
    assert body.status_code == 200
    assert body.json()["token_total"] > 0


# --- author facets ---------------------------------------------------------
# docs/data-processing.md. Two shapes to prove here, because they take
# different code paths: `gender` is a column one chained outer join past
# document_metadata, while `mother_tongue` is multi-valued and answered with
# EXISTS against a child table.


@pytest.fixture
def authored(seeded_corpus):
    """doc1 -> a two-tongue Male writer, doc2 -> a one-tongue Female writer.

    The two-tongue writer is the whole point of the fixture: it is the case a
    join through the child table would double-count.
    """
    from main.models import Author, AuthorMotherTongue, DocumentMetadata

    DocumentMetadata.objects.all().delete()
    Author.objects.all().delete()

    bilingual = Author.objects.create(
        university="TUFS", name_code="NEGSEI", gender="Male", source_file="t.csv",
    )
    for tongue in ("Japanese", "Malay"):
        AuthorMotherTongue.objects.create(author=bilingual, tongue=tongue)

    monolingual = Author.objects.create(
        university="TUFS", name_code="FUYU", gender="Female", source_file="t.csv",
    )
    AuthorMotherTongue.objects.create(author=monolingual, tongue="Japanese")

    DocumentMetadata.objects.create(
        source_filename="annotated.txt", match_key="annotated",
        document_id=seeded_corpus["doc1_id"],
        university="TUFS", year=2023, grade=3, name_code="NEGSEI",
        author=bilingual, source_file="t.csv",
    )
    DocumentMetadata.objects.create(
        source_filename="plain.txt", match_key="plain",
        document_id=seeded_corpus["doc2_id"],
        university="TUFS", year=2023, grade=2, name_code="FUYU",
        author=monolingual, source_file="t.csv",
    )
    yield seeded_corpus
    DocumentMetadata.objects.all().delete()
    Author.objects.all().delete()


@pytest.mark.parametrize("path,extra", ENDPOINTS)
def test_every_endpoint_accepts_the_author_facet_params(
    client, auth_headers, authored, path, extra
):
    """Same reason the catalogue facets get this test: the params are declared
    once on a shared Depends() object, so each endpoint has to be checked to
    actually bind them."""
    response = client.get(
        path,
        params={
            "corpus_ids": authored["corpus_id"],
            "gender": "Male",
            "mother_tongue": "Japanese",
            **extra,
        },
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("path,extra", ENDPOINTS)
def test_unset_sentinel_is_accepted_on_the_author_facets(
    client, auth_headers, authored, path, extra
):
    response = client.get(
        path,
        params={
            "corpus_ids": authored["corpus_id"],
            "gender": METADATA_UNSET,
            "mother_tongue": METADATA_UNSET,
            **extra,
        },
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text


def _total(client, auth_headers, corpus_id, **params):
    return client.get(
        "/api/v1/frequency",
        params={"corpus_ids": corpus_id, **params},
        headers=auth_headers,
    ).json()["token_total"]


def test_gender_narrows_through_the_chained_outer_join(client, auth_headers, authored):
    corpus_id = authored["corpus_id"]
    total = _total(client, auth_headers, corpus_id)

    male = _total(client, auth_headers, corpus_id, gender="Male")
    female = _total(client, auth_headers, corpus_id, gender="Female")

    assert 0 < male < total
    assert 0 < female < total
    assert male + female == total


def test_mother_tongue_matches_either_tongue_of_a_bilingual_writer(
    client, auth_headers, authored
):
    corpus_id = authored["corpus_id"]
    malay = _total(client, auth_headers, corpus_id, mother_tongue="Malay")
    japanese = _total(client, auth_headers, corpus_id, mother_tongue="Japanese")

    # Only the bilingual writer has Malay; both writers have Japanese.
    assert 0 < malay < japanese


def test_tongues_are_ORed_within_the_facet(client, auth_headers, authored):
    corpus_id = authored["corpus_id"]
    japanese = _total(client, auth_headers, corpus_id, mother_tongue="Japanese")
    both = _total(
        client, auth_headers, corpus_id, mother_tongue=["Japanese", "Malay"],
    )

    # The bilingual writer is already in the Japanese set, so OR-ing Malay in
    # must not add anything — and must certainly not add them twice.
    assert both == japanese


def test_a_bilingual_writer_is_not_counted_twice(client, auth_headers, authored):
    """The reason _mother_tongue_condition uses EXISTS rather than joining
    main_authormothertongue: a join yields two rows for this writer, and the
    ranked features aggregate over the resolved document set."""
    corpus_id = authored["corpus_id"]

    by_tongue = _total(client, auth_headers, corpus_id, mother_tongue="Malay")
    by_name = _total(client, auth_headers, corpus_id, name_code="NEGSEI")

    assert by_tongue == by_name
    assert by_tongue > 0


def test_author_facets_AND_across_and_OR_within(client, auth_headers, authored):
    corpus_id = authored["corpus_id"]

    assert _total(
        client, auth_headers, corpus_id, mother_tongue="Malay", gender="Male",
    ) > 0
    # Malay belongs to the Male writer only, so this intersection is empty.
    assert _total(
        client, auth_headers, corpus_id, mother_tongue="Malay", gender="Female",
    ) == 0


def test_author_values_and_unset_partition_the_corpus(client, auth_headers, authored):
    """Union + unset = total, for both author facets.

    For mother_tongue this must be the UNION: the bilingual writer appears
    under two values, so summing per-value totals would over-count and look
    like a bug when it isn't.
    """
    corpus_id = authored["corpus_id"]
    total = _total(client, auth_headers, corpus_id)

    tongue_union = _total(
        client, auth_headers, corpus_id, mother_tongue=["Japanese", "Malay"],
    )
    tongue_unset = _total(
        client, auth_headers, corpus_id, mother_tongue=METADATA_UNSET,
    )
    assert tongue_union + tongue_unset == total

    gender_values = _total(
        client, auth_headers, corpus_id, gender=["Female", "Male"],
    )
    gender_unset = _total(client, auth_headers, corpus_id, gender=METADATA_UNSET)
    assert gender_values + gender_unset == total


def test_facets_endpoint_serves_the_author_vocabularies(client, auth_headers, authored):
    body = client.get("/api/v1/metadata-facets", headers=auth_headers).json()

    assert body["genders"] == ["Female", "Male"]
    assert body["mother_tongues"] == ["Japanese", "Malay"]


def test_facets_endpoint_omits_author_values_with_no_uploaded_file(
    client, auth_headers, authored
):
    """Same dead-end rule the catalogue facets follow. This is why the real
    corpus's control offers neither Arabic nor Korean: both are genuinely
    recorded in the surveys, but neither speaker has a catalogued essay."""
    from main.models import Author, AuthorMotherTongue

    ghost = Author.objects.create(
        university="SFC", name_code="GHOST", gender="Other", source_file="t.csv",
    )
    AuthorMotherTongue.objects.create(author=ghost, tongue="Korean")

    body = client.get("/api/v1/metadata-facets", headers=auth_headers).json()

    assert "Korean" not in body["mother_tongues"]
    assert "Other" not in body["genders"]


def test_no_author_filter_leaves_the_query_untouched(client, auth_headers, authored):
    """resolve_document_ids only joins the author table when an author facet
    is actually filtered, so a catalogue-only filter must behave exactly as it
    did before this feature existed."""
    corpus_id = authored["corpus_id"]

    assert _total(client, auth_headers, corpus_id, university="TUFS") == _total(
        client, auth_headers, corpus_id,
    )
