"""Endpoint-level test for GET /api/v1/ngrams — parity-checked live against
Django's /analysis/ngrams/export/ (n=3 and n=5) during development. client/
auth_headers/seeded_corpus fixtures live in conftest.py."""


def test_requires_authentication(client, seeded_corpus):
    response = client.get("/api/v1/ngrams", params={"corpus_ids": [seeded_corpus["corpus_id"]]})
    assert response.status_code == 401


def test_default_n_is_3(client, auth_headers, seeded_corpus):
    response = client.get("/api/v1/ngrams", params={"corpus_ids": [seeded_corpus["corpus_id"]]}, headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["n"] == 3
    by_item = {row["item"]: row["count"] for row in body["results"]}
    assert by_item["suka nasi goreng"] == 3


def test_explicit_n(client, auth_headers, seeded_corpus):
    response = client.get(
        "/api/v1/ngrams", params={"corpus_ids": [seeded_corpus["corpus_id"]], "n": 5}, headers=auth_headers
    )
    body = response.json()
    assert body["n"] == 5
    assert all(len(row["item"].split(" ")) == 5 for row in body["results"])


def test_invalid_n_falls_back_to_3_like_the_legacy_view(client, auth_headers, seeded_corpus):
    # Mirrors main/views.py::_get_ngram_size — an n outside {2,3,4,5} falls
    # back to the default 3, not clamped to the nearest valid value (unlike
    # KWIC's window, which IS clamped — n-gram size is a discrete set, not
    # a continuous range).
    response = client.get(
        "/api/v1/ngrams", params={"corpus_ids": [seeded_corpus["corpus_id"]], "n": 99}, headers=auth_headers
    )
    assert response.json()["n"] == 3
