"""Endpoint-level test for GET /api/v1/epic (Error Phrase In Context) —
same shape as test_kwic_router.py. client/auth_headers/seeded_corpus
fixtures live in conftest.py."""


def test_requires_authentication(client, seeded_corpus):
    response = client.get("/api/v1/epic", params={"corpus_ids": [seeded_corpus["error_corpus_id"]]})
    assert response.status_code == 401


def test_lists_occurrences_with_context_and_correction(client, auth_headers, seeded_corpus):
    response = client.get(
        "/api/v1/epic",
        params={"corpus_ids": [seeded_corpus["error_corpus_id"]], "error_code": ["urtfn"], "window": 2},
        headers=auth_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["result_count"] == 1
    hit = body["results"][0]
    assert hit["keyword"] == ["goreng", "nasi"]
    assert hit["left"] == ["saya", "suka"]
    assert hit["right"] == ["sekali", "lagi"]
    assert hit["error_code"] == "urtfn"
    assert hit["category_path"] == "eror;gramatikal;frasa-nomina;urtfn"
    assert hit["correction_text"] == "nasi goreng"


def test_excludes_unresolved_annotations(client, auth_headers, seeded_corpus):
    response = client.get(
        "/api/v1/epic", params={"corpus_ids": [seeded_corpus["error_corpus_id"]]}, headers=auth_headers,
    )
    body = response.json()
    assert body["result_count"] == 3  # never the unresolved "eror;doesnotexist" segment


def test_window_is_clamped_to_the_documented_range(client, auth_headers, seeded_corpus):
    response = client.get(
        "/api/v1/epic",
        params={"corpus_ids": [seeded_corpus["error_corpus_id"]], "window": 999},
        headers=auth_headers,
    )
    assert response.json()["window"] == 10


def test_missing_corpus_ids_is_a_validation_error(client, auth_headers):
    response = client.get("/api/v1/epic", headers=auth_headers)
    assert response.status_code == 422


def test_unknown_corpus_id_returns_empty_results_not_an_error(client, auth_headers):
    response = client.get("/api/v1/epic", params={"corpus_ids": [999999]}, headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["result_count"] == 0
