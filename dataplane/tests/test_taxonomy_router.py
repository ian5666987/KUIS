"""Endpoint-level test for GET /api/v1/taxonomy. client/auth_headers/
seeded_corpus fixtures live in conftest.py (seeded_corpus loads the real
taxonomy via load_error_taxonomy — depended on here only to guarantee that
command has run before this test, not for its documents/corpus)."""


def test_requires_authentication(client, seeded_corpus):
    response = client.get("/api/v1/taxonomy")
    assert response.status_code == 401


def test_returns_the_four_top_categories_with_nested_leaves(client, auth_headers, seeded_corpus):
    response = client.get("/api/v1/taxonomy", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    codes = {node["code"] for node in body}
    assert codes == {"leksikal", "gramatikal", "ejaan", "lainnya"}

    ejaan = next(node for node in body if node["code"] == "ejaan")
    leaf_codes = {child["code"] for child in ejaan["children"]}
    assert "ejk" in leaf_codes
    ejk = next(child for child in ejaan["children"] if child["code"] == "ejk")
    assert ejk["is_leaf"] is True
    assert ejk["path"] == "eror;ejaan;ejk"
    assert ejk["gloss"]
