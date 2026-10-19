"""
Token staleness — which documents' Token rows no longer reflect the document
as it is now, read by the tokenizer as it is now (docs/data-processing.md §3)
— and the task that re-queues them, which celery beat dispatches hourly
(worker/celery_app.py). `manage.py retokenize` runs the same check
synchronously.

A document is stale for one of three reasons, checked in this order:

    untokenized         tokenized_hash is empty. The rows were never built (an
                        upload no worker ever picked up), or an index_document
                        run cleared it and never finished, or crashed
                        (REASON_WORKER_ERROR) — index_document clears it before
                        it starts replacing the rows.
    tokenizer_outdated  built by an older indexing.TOKENIZER_VERSION than the
                        code now running.
    content_changed     built from different content than the document now
                        holds — edited in the Django admin, the shell or SQL,
                        none of which reindex.

content_changed is decided by hashing `content` afresh, not by comparing
tokenized_hash against the stored content_hash column. Document.save() keeps
content_hash in step with `content`, but an edit that bypasses save() — a
QuerySet.update(), raw SQL — changes `content` and leaves content_hash as it
was, and the column comparison (`tokenized_hash IS DISTINCT FROM content_hash`)
cannot see that edit. Hashing means reading every document's content, so the
scan streams it in chunks rather than loading the corpus into memory.

A document that failed on its content (malformed XML, no words) is NOT stale:
index_document records the hash it failed on, so it is retried only once the
content or the tokenizer changes — fixing a broken file in the admin is enough
for the next sweep to pick it up.
"""

from collections import Counter

from main.document_ingest import compute_content_hash
from main.models import Document
from worker.celery_app import app
from worker.tasks.indexing import TOKENIZER_VERSION, index_document

STALE_UNTOKENIZED = "untokenized"
STALE_TOKENIZER_OUTDATED = "tokenizer_outdated"
STALE_CONTENT_CHANGED = "content_changed"

# Rows fetched per round trip while scanning. Every row carries a document's
# full content, so this caps the scan's memory at this many documents' text,
# whatever the size of the corpus.
SCAN_CHUNK_SIZE = 100


def find_stale_documents(documents=None) -> dict[int, str]:
    """{document_id: reason} for every document in `documents` (a Document
    queryset; all documents by default) whose Token rows are stale, in id
    order. Read-only."""
    if documents is None:
        documents = Document.objects.all()

    rows = (
        documents.order_by("id")
        .values_list("id", "content", "tokenized_hash", "tokenizer_version")
        .iterator(chunk_size=SCAN_CHUNK_SIZE)
    )

    stale = {}
    for document_id, content, tokenized_hash, tokenizer_version in rows:
        if not tokenized_hash:
            stale[document_id] = STALE_UNTOKENIZED
        elif tokenizer_version is None or tokenizer_version < TOKENIZER_VERSION:
            stale[document_id] = STALE_TOKENIZER_OUTDATED
        elif compute_content_hash(content) != tokenized_hash:
            stale[document_id] = STALE_CONTENT_CHANGED
    return stale


@app.task
def reindex_stale_documents() -> dict:
    """Queues index_document for every stale document, which rebuilds its
    tokens and then its aggregates like any upload. Returns how many it queued
    per reason, and how many documents it skipped for being at 'indexing'.

    Documents at 'indexing' are skipped. Each either has an index_document
    queued or running, which will rebuild it anyway, or lost one (a killed
    worker, a message dropped while Redis was down), and nothing in the row
    tells the two apart — while queuing a second run for every file of an
    upload batch still in the queue would double the backlog. `manage.py
    retokenize` does include them; whether a worker is still working through a
    batch is the operator's call to make. Queuing a document twice (two sweeps
    before its first run starts) costs a redundant run, nothing more —
    index_document serializes runs per document."""
    stale = find_stale_documents(Document.objects.exclude(status=Document.STATUS_INDEXING))
    for document_id in stale:
        index_document.delay(document_id)

    return {
        "queued": dict(Counter(stale.values())),
        "skipped_indexing": Document.objects.filter(status=Document.STATUS_INDEXING).count(),
    }
