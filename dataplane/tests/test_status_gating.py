"""Document.status scopes every feature (dataplane/repositories/shared.py::
resolve_document_ids): KWIC reads only main_token, so it accepts 'tokenized'
or 'ready'; every other feature reads an aggregate or the error tables and
accepts 'ready' only.

Four documents, one per status, each with a word that appears in no other
document so every assertion can tell exactly which documents a feature saw.
The 'indexing' and 'failed' documents are fully indexed and THEN have their
status overwritten, so their Token and aggregate rows all exist. That pins
the gate itself: they are excluded because of their status, not because a
missing row happens to make them invisible. (The same situation arises for
real: a 'ready' document being re-indexed still has its old aggregates until
aggregate_document replaces them.)
"""

from unittest import mock

import pytest

from dataplane.repositories.shared import TOKEN_STATUSES, resolve_document_ids


def _segment_doc(word: str) -> str:
    return (
        "<document><header><textfile>t</textfile><lang>indonesian</lang></header><body>"
        f"kata <segment id='1' features='eror;ejaan;ejk'>{word}</segment> muncul"
        "</body></document>"
    )


@pytest.fixture(scope="module")
def gated_corpus(seeded_corpus):
    from main.models import Corpus, Document
    from worker.tasks.indexing import index_document

    def indexed(title, word, status=None):
        doc = Document.objects.create(title=title, content=_segment_doc(word))
        index_document(doc.id)
        if status:
            Document.objects.filter(id=doc.id).update(status=status)
        return doc

    ready = indexed("ready.xml", "readyonly")
    indexing = indexed("indexing.xml", "indexingonly", status=Document.STATUS_INDEXING)
    failed = indexed("failed.xml", "failedonly", status=Document.STATUS_FAILED)

    # A real tokenized document: index_document ran, aggregate_document is
    # still queued — Token rows and nothing else.
    tokenized = Document.objects.create(title="tokenized.xml", content=_segment_doc("tokenizedonly"))
    with mock.patch("worker.tasks.indexing.aggregate_document.delay"):
        index_document(tokenized.id)

    corpus = Corpus.objects.create(name="Status gating corpus")
    corpus.documents.set([ready, indexing, failed, tokenized])

    return {
        "corpus_id": corpus.id,
        "ready_id": ready.id,
        "tokenized_id": tokenized.id,
    }


async def test_resolve_defaults_to_ready_only(session, gated_corpus):
    ids = await resolve_document_ids(session, [gated_corpus["corpus_id"]])
    assert ids == [gated_corpus["ready_id"]]


async def test_resolve_with_token_statuses_adds_tokenized(session, gated_corpus):
    ids = await resolve_document_ids(session, [gated_corpus["corpus_id"]], statuses=TOKEN_STATUSES)
    assert sorted(ids) == sorted([gated_corpus["ready_id"], gated_corpus["tokenized_id"]])


@pytest.mark.parametrize(
    ("word", "expected"),
    [("readyonly", 1), ("tokenizedonly", 1), ("indexingonly", 0), ("failedonly", 0)],
)
def test_kwic_sees_tokenized_and_ready(client, auth_headers, gated_corpus, word, expected):
    response = client.get(
        "/api/v1/kwic", params={"corpus_ids": [gated_corpus["corpus_id"]], "q": word}, headers=auth_headers
    )
    assert response.status_code == 200
    assert response.json()["result_count"] == expected


def test_frequency_sees_ready_only(client, auth_headers, gated_corpus):
    response = client.get("/api/v1/frequency", params={"corpus_ids": [gated_corpus["corpus_id"]]}, headers=auth_headers)
    body = response.json()

    items = {row["item"] for row in body["results"]}
    assert items == {"kata", "readyonly", "muncul"}
    # The denominator excludes the other documents too, not just their words.
    assert body["token_total"] == 3


def test_ngrams_see_ready_only(client, auth_headers, gated_corpus):
    response = client.get("/api/v1/ngrams", params={"corpus_ids": [gated_corpus["corpus_id"]]}, headers=auth_headers)
    items = {row["item"] for row in response.json()["results"]}
    assert items == {"kata readyonly muncul"}  # the endpoint's default n is 3


def test_error_frequency_sees_ready_only(client, auth_headers, gated_corpus):
    response = client.get(
        "/api/v1/error-frequency", params={"corpus_ids": [gated_corpus["corpus_id"]]}, headers=auth_headers
    )
    phrases = {row["phrase"] for row in response.json()["results"]}
    assert phrases == {"readyonly"}


def test_epic_sees_ready_only(client, auth_headers, gated_corpus):
    response = client.get("/api/v1/epic", params={"corpus_ids": [gated_corpus["corpus_id"]]}, headers=auth_headers)
    body = response.json()
    assert body["result_count"] == 1
    assert body["results"][0]["keyword"] == ["readyonly"]


def test_error_summary_sees_ready_only(client, auth_headers, gated_corpus):
    response = client.get(
        "/api/v1/error-summary", params={"corpus_ids": [gated_corpus["corpus_id"]]}, headers=auth_headers
    )
    assert response.json()["total"] == 1
