"""
Endpoint-level test for GET /api/v1/kwic through the real FastAPI app
(architecture plan §8 Phase 2's verification step), reusing the same
seeded-Postgres fixture as test_kwic_repository.py. This is the in-process
equivalent of the parity check run manually during development, comparing
this endpoint's output against Django's live `/analysis/kwic/search/export/`
and `/analysis/kwic/export/` CSV endpoints for the same query — see
docs/new-architecture.md's Phase 2 status entry for that comparison's
result (byte-for-byte match).
"""

import time

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from dataplane.core.config import settings
from dataplane.core.db import get_db_session
from dataplane.main import app
from dataplane.tests.test_kwic_repository import seeded_corpus  # noqa: F401 (fixture)

JWT_TEST_SECRET = "test-jwt-secret-for-dataplane-router-tests"


@pytest.fixture(autouse=True)
def jwt_secret_configured(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret", JWT_TEST_SECRET)


@pytest.fixture
def client():
    # dataplane.core.db's module-level `engine` pools connections against
    # whichever event loop existed when it was first checked out — fine in
    # a real long-running uvicorn process (one loop for the process
    # lifetime), but TestClient spins a fresh loop per test function, and a
    # pooled connection from a previous test's now-closed loop blows up on
    # its next checkout ("RuntimeError: Event loop is closed"). NullPool
    # sidesteps this by never holding a connection open between checkouts —
    # test-only, the production engine keeps its normal pooling.
    test_engine = create_async_engine(settings.database_url, poolclass=NullPool)
    test_session_factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def override_get_db_session():
        async with test_session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_get_db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.fixture
def auth_headers():
    now = int(time.time())
    token = jwt.encode(
        {
            "token_type": "access",
            "exp": now + 900,
            "iat": now,
            "user_id": "1",
            "is_staff": False,
            "username": "researcher",
            "email": "researcher@example.com",
        },
        JWT_TEST_SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


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
