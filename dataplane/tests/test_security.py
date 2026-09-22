"""
Unit tests for dataplane/core/security.py's stateless JWT verification
(architecture plan §3). Tokens here are hand-crafted with PyJWT to match the
exact claim shape djangorestframework-simplejwt produces (see
main/api_auth.py and main/tests.py::JWTAuthTests for the Django side) —
deliberately NOT going through a live Django process, so these tests don't
require Django to run. See test_jwt_consistency.py for the one check that
does cross-reference Django's config, to catch the two secrets drifting.
"""

import time

import jwt
import pytest
from fastapi import HTTPException

from dataplane.core import security
from dataplane.core.config import settings

SECRET = "test-jwt-secret-for-dataplane-unit-tests"


def _make_token(*, secret=SECRET, token_type="access", exp_delta=900, **claim_overrides):
    now = int(time.time())
    claims = {
        "token_type": token_type,
        "exp": now + exp_delta,
        "iat": now,
        "jti": "test-jti",
        "user_id": "1",
        "is_staff": True,
        "username": "researcher",
        "email": "researcher@example.com",
    }
    claims.update(claim_overrides)
    return jwt.encode(claims, secret, algorithm="HS256")


@pytest.fixture(autouse=True)
def jwt_secret_configured(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret", SECRET)


def test_valid_token_decodes_to_authenticated_user():
    user = security.decode_access_token(_make_token())

    assert user == security.AuthenticatedUser(
        id=1, username="researcher", email="researcher@example.com", is_staff=True
    )


def test_non_staff_claim_carries_through():
    user = security.decode_access_token(_make_token(is_staff=False))
    assert user.is_staff is False


def test_expired_token_is_rejected():
    with pytest.raises(HTTPException) as exc_info:
        security.decode_access_token(_make_token(exp_delta=-60))
    assert exc_info.value.status_code == 401


def test_wrong_signing_key_is_rejected():
    # Simulates KUIS-FE's JWT_SECRET drifting from the KUIS backend's, or an
    # attacker without the shared secret — either way, must not verify.
    with pytest.raises(HTTPException) as exc_info:
        security.decode_access_token(_make_token(secret="a-different-secret"))
    assert exc_info.value.status_code == 401


def test_refresh_token_presented_as_access_token_is_rejected():
    # A refresh token is signed with the same key/algorithm as an access
    # token — only token_type differs. Accepting one where an access token
    # is expected would let a 7-day refresh token stand in for a 15-minute
    # access token everywhere.
    with pytest.raises(HTTPException) as exc_info:
        security.decode_access_token(_make_token(token_type="refresh"))
    assert exc_info.value.status_code == 401


@pytest.mark.parametrize("missing_claim", ["user_id", "is_staff", "username", "email"])
def test_token_missing_a_required_claim_is_rejected(missing_claim):
    now = int(time.time())
    claims = {
        "token_type": "access",
        "exp": now + 900,
        "iat": now,
        "jti": "test-jti",
        "user_id": "1",
        "is_staff": True,
        "username": "researcher",
        "email": "researcher@example.com",
    }
    del claims[missing_claim]
    token = jwt.encode(claims, SECRET, algorithm="HS256")

    with pytest.raises(HTTPException) as exc_info:
        security.decode_access_token(token)
    assert exc_info.value.status_code == 401


def test_missing_jwt_secret_fails_loudly_not_as_a_bad_token(monkeypatch):
    # Distinguishes "misconfigured deployment" (500, fix the environment)
    # from "bad/expired token" (401, the client's problem) — see the comment
    # in security.py for why this check exists ahead of jwt.decode.
    monkeypatch.setattr(settings, "jwt_secret", "")

    with pytest.raises(HTTPException) as exc_info:
        security.decode_access_token(_make_token())
    assert exc_info.value.status_code == 500
