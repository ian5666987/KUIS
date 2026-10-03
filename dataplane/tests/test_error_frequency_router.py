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


def test_category_code_selects_every_leaf_descendant(client, auth_headers, seeded_corpus):
    # error_code doubles as the "select this whole category" mechanism —
    # a tree-picker checking "gramatikal" sends just that one code.
    response = client.get(
        "/api/v1/error-frequency",
        params={"corpus_ids": [seeded_corpus["error_corpus_id"]], "error_code": ["gramatikal"]},
        headers=auth_headers,
    )
    body = response.json()
    assert [row["phrase"] for row in body["results"]] == ["goreng nasi"]


def test_multiple_codes_union_across_branches(client, auth_headers, seeded_corpus):
    response = client.get(
        "/api/v1/error-frequency",
        params={"corpus_ids": [seeded_corpus["error_corpus_id"]], "error_code": ["gramatikal", "ejaan"]},
        headers=auth_headers,
    )
    body = response.json()
    assert {row["phrase"] for row in body["results"]} == {"nasi", "goreng nasi", "Sayang"}


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


def test_error_summary_breaks_down_every_node_in_the_hierarchy(client, auth_headers, seeded_corpus):
    response = client.get(
        "/api/v1/error-summary", params={"corpus_ids": [seeded_corpus["error_corpus_id"]]}, headers=auth_headers,
    )

    body = response.json()
    by_node = {row["code"]: row for row in body["by_node"]}
    # The frontend's expandable summary tree traverses these — intermediate
    # nodes included, each already subtree-inclusive so nothing is summed
    # client-side.
    assert by_node["gramatikal"]["count"] == 1
    assert by_node["frasa-nomina"]["count"] == 1
    assert by_node["urtfn"]["count"] == 1
    assert by_node["urtfn"]["self_count"] == 1
    assert by_node["gramatikal"]["self_count"] == 0
    assert by_node["ejaan"]["path"] == "eror;ejaan"


def test_error_summary_orders_nodes_by_count_descending(client, auth_headers, seeded_corpus):
    response = client.get(
        "/api/v1/error-summary", params={"corpus_ids": [seeded_corpus["error_corpus_id"]]}, headers=auth_headers,
    )

    counts = [row["count"] for row in response.json()["by_node"]]
    assert counts == sorted(counts, reverse=True)


def test_error_summary_requires_authentication(client, seeded_corpus):
    response = client.get("/api/v1/error-summary", params={"corpus_ids": [seeded_corpus["error_corpus_id"]]})
    assert response.status_code == 401
