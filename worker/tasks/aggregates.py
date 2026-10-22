"""
Tier-2 precomputed aggregates (architecture plan §2/§5) — populated by the
worker, read by dataplane/repositories/postgres/frequency_repository.py
instead of scanning raw Token rows at query time.

The task fully replaces (delete-then-insert) a document's rows rather than
incrementally upserting, so re-running it is always safe.

There used to be a second task here, compute_document_ngrams, writing the
top 500 n-grams per (document, n) to DocumentNgram. Migration
0019_drop_document_ngram removed it: n-grams and collocations now count
windows straight from Token (dataplane/repositories/postgres/
_window_ngram_query.py), which measured faster, exact, and 62% smaller
(docs/hosting.md §3).
"""

from django.db.models import Count

from main.models import DocumentWordFreq, Token
from worker.celery_app import app


@app.task
def compute_document_word_freq(document_id: int) -> int:
    """COMPLETE per-document word counts — no top-K here, vocabulary size
    is bounded (see DocumentWordFreq's docstring in main/models.py)."""
    DocumentWordFreq.objects.filter(document_id=document_id).delete()

    counts = (
        Token.objects.filter(document_id=document_id)
        .values("word_type_id")
        .annotate(count=Count("id"))
    )
    rows = [
        DocumentWordFreq(document_id=document_id, word_type_id=row["word_type_id"], count=row["count"])
        for row in counts
    ]

    DocumentWordFreq.objects.bulk_create(rows, batch_size=5000)
    return len(rows)

