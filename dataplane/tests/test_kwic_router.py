"""
Endpoint-level test for GET /api/v1/kwic through the real FastAPI app
(architecture plan §8 Phase 2's verification step). This is the in-process
equivalent of the parity check run manually during development, comparing
this endpoint's output against Django's live `/analysis/kwic/search/export/`
and `/analysis/kwic/export/` CSV endpoints for the same query — see
docs/new-architecture.md's Phase 2 status entry for that comparison's
result (byte-for-byte match). seeded_corpus/client/auth_headers fixtures
live in conftest.py.
"""


def test_requires_authentication(client, seeded_corpus):
    response = client.get("/api/v1/kwic", params={"corpus_ids": [seeded_corpus["corpus_id"]], "q": "nasi"})
    assert response.status_code == 401


def test_single_word_search_matches_repository_behavior(client, auth_headers, seeded_corpus):
    response = client.get(
        "/api/v1/kwic",
        params={"corpus_ids": [seeded_corpus["corpus_id"]], "q": "nasi", "window": 2},
        headers=auth_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["result_count"] == 4
    assert len(body["results"]) == 4
    assert body["window"] == 2
    assert body["corrected"] is False
    assert all(hit["keyword"] == ["nasi"] for hit in body["results"])


def test_phrase_search(client, auth_headers, seeded_corpus):
    response = client.get(
        "/api/v1/kwic",
        params={"corpus_ids": [seeded_corpus["corpus_id"]], "q": "nasi goreng", "window": 3},
        headers=auth_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["result_count"] == 4
    assert all(hit["keyword"] == ["nasi", "goreng"] for hit in body["results"])


def test_corrected_mode(client, auth_headers, seeded_corpus):
    response = client.get(
        "/api/v1/kwic",
        params={"corpus_ids": [seeded_corpus["corpus_id"]], "q": "tetapi", "corrected": True},
        headers=auth_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["corrected"] is True
    assert body["result_count"] == 1


def test_window_is_clamped_to_the_documented_range(client, auth_headers, seeded_corpus):
    # Mirrors main/views.py::_get_window's clamping (KWIC_WINDOW_MIN=2,
    # KWIC_WINDOW_MAX=10) — the service clamps rather than rejecting, same
    # as the legacy view.
    response = client.get(
        "/api/v1/kwic",
        params={"corpus_ids": [seeded_corpus["corpus_id"]], "q": "nasi", "window": 999},
        headers=auth_headers,
    )
    assert response.json()["window"] == 10

    response = client.get(
        "/api/v1/kwic",
        params={"corpus_ids": [seeded_corpus["corpus_id"]], "q": "nasi", "window": 0},
        headers=auth_headers,
    )
    assert response.json()["window"] == 2


def test_missing_corpus_ids_is_a_validation_error(client, auth_headers):
    response = client.get("/api/v1/kwic", params={"q": "nasi"}, headers=auth_headers)
    assert response.status_code == 422


def test_unknown_corpus_id_returns_empty_results_not_an_error(client, auth_headers):
    response = client.get("/api/v1/kwic", params={"corpus_ids": [999999], "q": "nasi"}, headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["result_count"] == 0
