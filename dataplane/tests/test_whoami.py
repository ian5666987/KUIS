"""
Endpoint-level test for GET /api/v1/whoami through a real FastAPI app
instance (architecture plan §3, §8 Phase 1's verification step) — the
in-process equivalent of the manual curl-based end-to-end check run against
a live Django + FastAPI pair during development.
"""

import time

import jwt
import pytest
from fastapi.testclient import TestClient

from dataplane.core.config import settings
from dataplane.main import app

SECRET = "test-jwt-secret-for-dataplane-unit-tests"


@pytest.fixture(autouse=True)
def jwt_secret_configured(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret", SECRET)


@pytest.fixture
def client():
    return TestClient(app)


def _access_token(**overrides):
    now = int(time.time())
    claims = {
        "token_type": "access",
        "exp": now + 900,
        "iat": now,
        "user_id": "1",
        "is_staff": True,
        "username": "researcher",
        "email": "researcher@example.com",
    }
    claims.update(overrides)
    return jwt.encode(claims, SECRET, algorithm="HS256")


def test_health_requires_no_auth(client):
    # Container/orchestrator health checks shouldn't need a bearer token.
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_whoami_without_a_token_is_rejected(client):
    response = client.get("/api/v1/whoami")
    assert response.status_code == 401


def test_whoami_with_a_valid_token_returns_the_claims(client):
    response = client.get("/api/v1/whoami", headers={"Authorization": f"Bearer {_access_token()}"})

    assert response.status_code == 200
    assert response.json() == {
        "id": 1,
        "username": "researcher",
        "email": "researcher@example.com",
        "is_staff": True,
    }


def test_whoami_with_garbage_token_is_rejected(client):
    response = client.get("/api/v1/whoami", headers={"Authorization": "Bearer garbage.not.a.jwt"})
    assert response.status_code == 401
