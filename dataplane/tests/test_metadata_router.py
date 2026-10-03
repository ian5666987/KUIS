"""Endpoint-level tests for the secondary metadata filter
(docs/metadata-catalogue-plan.md): GET /api/v1/metadata-facets, and the filter
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
