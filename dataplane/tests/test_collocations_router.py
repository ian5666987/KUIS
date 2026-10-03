"""Endpoint-level test for GET /api/v1/collocations — parity-checked live
against Django's /analysis/collocations/export/ during development. client/
auth_headers/seeded_corpus fixtures live in conftest.py."""


def test_requires_authentication(client, seeded_corpus):
    response = client.get("/api/v1/collocations", params={"corpus_ids": [seeded_corpus["corpus_id"]]})
    assert response.status_code == 401


def test_lists_adjacent_pairs(client, auth_headers, seeded_corpus):
    response = client.get(
        "/api/v1/collocations", params={"corpus_ids": [seeded_corpus["corpus_id"]]}, headers=auth_headers
    )

    assert response.status_code == 200
    body = response.json()
    assert body["type_count"] == 12
    # token_total is the WORD count, not the pair count — see
    # _ranked_query.py's docstring on why these legitimately differ.
    assert body["token_total"] == 22
    by_item = {row["item"]: row["count"] for row in body["results"]}
    assert by_item["nasi goreng"] == 4


def test_pairs_never_cross_a_document_boundary(client, auth_headers, seeded_corpus):
    response = client.get(
        "/api/v1/collocations",
        params={"corpus_ids": [seeded_corpus["corpus_id"]], "per_page": 250},
        headers=auth_headers,
    )
    items = {row["item"] for row in response.json()["results"]}
    assert "lagi saya" not in items
