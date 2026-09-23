"""Endpoint-level test for GET /api/v1/frequency — parity-checked live
against Django's /analysis/frequency/export/ during development (see
docs/new-architecture.md's Phase 4 status entry: exact set AND order match,
including the count-tie alphabetical tie-break). client/auth_headers/
seeded_corpus fixtures live in conftest.py."""


def test_requires_authentication(client, seeded_corpus):
    response = client.get("/api/v1/frequency", params={"corpus_ids": [seeded_corpus["corpus_id"]]})
    assert response.status_code == 401


def test_lists_word_frequencies(client, auth_headers, seeded_corpus):
    response = client.get(
        "/api/v1/frequency", params={"corpus_ids": [seeded_corpus["corpus_id"]]}, headers=auth_headers
    )

    assert response.status_code == 200
    body = response.json()
    assert body["type_count"] == 9
    assert body["token_total"] == 22
    by_item = {row["item"]: row["count"] for row in body["results"]}
    assert by_item["nasi"] == 4


def test_default_sort_is_count_desc_with_alphabetical_tie_break(client, auth_headers, seeded_corpus):
    response = client.get(
        "/api/v1/frequency", params={"corpus_ids": [seeded_corpus["corpus_id"]]}, headers=auth_headers
    )
    items = [row["item"] for row in response.json()["results"]]
    assert items[:4] == ["goreng", "nasi", "saya", "suka"]


def test_filter_by_match_mode(client, auth_headers, seeded_corpus):
    response = client.get(
        "/api/v1/frequency",
        params={"corpus_ids": [seeded_corpus["corpus_id"]], "q": "nasi", "match": "starts"},
        headers=auth_headers,
    )
    body = response.json()
    assert body["result_count"] == 1
    assert body["results"][0]["item"] == "nasi"
    # type_count stays UNFILTERED:
    assert body["type_count"] == 9


def test_corrected_mode(client, auth_headers, seeded_corpus):
    response = client.get(
        "/api/v1/frequency",
        params={"corpus_ids": [seeded_corpus["corpus_id"]], "corrected": True},
        headers=auth_headers,
    )
    by_item = {row["item"]: row["count"] for row in response.json()["results"]}
    assert "tapi" not in by_item
    assert by_item["tetapi"] == 1


def test_missing_corpus_ids_is_a_validation_error(client, auth_headers):
    response = client.get("/api/v1/frequency", headers=auth_headers)
    assert response.status_code == 422
