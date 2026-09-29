"""Endpoint-level test for GET /api/v1/error-frequency, same shape as
test_frequency_router.py. client/auth_headers/seeded_corpus fixtures live
in conftest.py; ERROR_CORPUS_XML's annotations are documented there and in
test_error_analytics_repository.py's module docstring."""


def test_requires_authentication(client, seeded_corpus):
    response = client.get("/api/v1/error-frequency", params={"corpus_ids": [seeded_corpus["error_corpus_id"]]})
    assert response.status_code == 401


def test_lists_error_phrases_excluding_unresolved(client, auth_headers, seeded_corpus):
    response = client.get(
        "/api/v1/error-frequency", params={"corpus_ids": [seeded_corpus["error_corpus_id"]]}, headers=auth_headers,
    )

    assert response.status_code == 200
    body = response.json()
    phrases = {row["phrase"] for row in body["results"]}
    assert phrases == {"nasi", "goreng nasi", "Sayang"}


def test_main_filter_is_or_across_error_codes(client, auth_headers, seeded_corpus):
    response = client.get(
        "/api/v1/error-frequency",
        params={"corpus_ids": [seeded_corpus["error_corpus_id"]], "error_code": ["ejk"]},
        headers=auth_headers,
    )
    body = response.json()
    by_phrase = {row["phrase"]: row["frequency"] for row in body["results"]}
    assert by_phrase == {"nasi": 1, "Sayang": 1}


def test_secondary_filter_is_and_across_categories(client, auth_headers, seeded_corpus):
    response = client.get(
        "/api/v1/error-frequency",
        params={"corpus_ids": [seeded_corpus["error_corpus_id"]], "category": ["gramatikal", "frasa-nomina"]},
        headers=auth_headers,
    )
    body = response.json()
    assert [row["phrase"] for row in body["results"]] == ["goreng nasi"]


def test_missing_corpus_ids_is_a_validation_error(client, auth_headers):
    response = client.get("/api/v1/error-frequency", headers=auth_headers)
    assert response.status_code == 422


def test_error_summary_groups_by_top_category(client, auth_headers, seeded_corpus):
    response = client.get(
        "/api/v1/error-summary", params={"corpus_ids": [seeded_corpus["error_corpus_id"]]}, headers=auth_headers,
    )

    assert response.status_code == 200
    body = response.json()
    by_category = {row["category"]: row["count"] for row in body["by_category"]}
    assert by_category == {"ejaan": 2, "gramatikal": 1}
    assert body["total"] == 3


def test_error_summary_requires_authentication(client, seeded_corpus):
    response = client.get("/api/v1/error-summary", params={"corpus_ids": [seeded_corpus["error_corpus_id"]]})
    assert response.status_code == 401
