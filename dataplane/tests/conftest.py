"""
Shared fixtures for every dataplane test that needs a real seeded Postgres
database (test_kwic_repository.py, test_kwic_router.py, test_ranked_query.py,
and Phase 4's frequency/collocation/ngram router tests) — moved here once a
third file needed the same `seeded_corpus`/`session`/`document_ids` trio
that test_kwic_repository.py originally defined for itself alone, and again
once a second router test file needed `client`/`auth_headers`. Available to
every file in this directory automatically; no import needed.

Safety guard lives on seeded_corpus: refuses to run unless DB_NAME contains
"test" — this fixture DELETES ALL Document/Corpus rows in whatever database
it's pointed at before seeding known fixture data. Point DB_NAME at a
dedicated, disposable test database (locally: `createdb kuis_dataplane_test`;
in CI: the ephemeral `postgres` service container — see
.github/workflows/ci.yml).
"""

import os
import time

import django
import jwt
import pytest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.core.management import call_command  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from dataplane.core.config import settings as dataplane_settings  # noqa: E402
from dataplane.core.db import async_session_factory, engine, get_db_session  # noqa: E402
from dataplane.main import app  # noqa: E402
from dataplane.repositories.shared import resolve_document_ids  # noqa: E402

JWT_TEST_SECRET = "test-jwt-secret-for-dataplane-router-tests"

CORPUS_XML = """<document>
  <header><textfile>sample</textfile><lang>indonesian</lang></header>
  <body>
    saya suka makan nasi goreng
    <segment id="1" features="eror;leksikal" Correction="tetapi">tapi</segment>
    saya tidak suka nasi goreng lagi
  </body>
</document>"""

PLAIN_TEXT = "saya suka nasi goreng dan saya suka nasi goreng lagi"


@pytest.fixture(scope="module")
def seeded_corpus():
    db_name = os.environ.get("DB_NAME", "")
    if "test" not in db_name.lower():
        pytest.fail(
            f"Refusing to run: DB_NAME={db_name!r} doesn't look like a test database "
            "(must contain 'test'). This fixture deletes all Document/Corpus rows."
        )

    call_command("migrate", "--noinput", verbosity=0)

    from main.models import Corpus, Document
    from worker.tasks.indexing import index_document

    Document.objects.all().delete()
    Corpus.objects.all().delete()

    annotated = Document.objects.create(title="annotated.xml", content=CORPUS_XML)
    plain = Document.objects.create(title="plain.txt", content=PLAIN_TEXT)
    corpus = Corpus.objects.create(name="Test corpus")
    corpus.documents.set([annotated, plain])

    # Tokenization (and Tier-2 aggregate computation) is no longer automatic
    # on save (architecture plan §5 removed the post_save signal) — .delay()
    # runs synchronously because CELERY_TASK_ALWAYS_EAGER=1 is set for this
    # test run, exercising the real indexing + aggregate tasks rather than a
    # hand-rolled test-only setup path that could drift from what
    # production actually populates.
    index_document.delay(annotated.id)
    index_document.delay(plain.id)

    return {"corpus_id": corpus.id, "doc1_id": annotated.id, "doc2_id": plain.id}


@pytest.fixture
async def session():
    async with async_session_factory() as s:
        yield s
    await engine.dispose()


@pytest.fixture
async def document_ids(seeded_corpus, session):
    return await resolve_document_ids(session, [seeded_corpus["corpus_id"]])


@pytest.fixture
def _jwt_secret_configured(monkeypatch):
    # Deliberately NOT autouse — test_jwt_consistency.py needs the REAL
    # configured secret from .env to compare against Django's, and an
    # earlier version of this fixture being autouse at the conftest level
    # (rather than scoped to test_kwic_router.py alone) broke exactly that
    # check by patching jwt_secret out from under it. `client` below
    # depends on this explicitly instead, so only tests that actually use
    # `client` get the patched test secret.
    monkeypatch.setattr(dataplane_settings, "jwt_secret", JWT_TEST_SECRET)


@pytest.fixture
def client(_jwt_secret_configured):
    # dataplane.core.db's module-level `engine` pools connections against
    # whichever event loop existed when it was first checked out — fine in
    # a real long-running uvicorn process (one loop for the process
    # lifetime), but TestClient spins a fresh loop per test function, and a
    # pooled connection from a previous test's now-closed loop blows up on
    # its next checkout ("RuntimeError: Event loop is closed"). NullPool
    # sidesteps this by never holding a connection open between checkouts —
    # test-only, the production engine keeps its normal pooling.
    test_engine = create_async_engine(dataplane_settings.database_url, poolclass=NullPool)
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
