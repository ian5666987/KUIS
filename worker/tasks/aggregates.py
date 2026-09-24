"""
Tier-2 precomputed aggregates (architecture plan §2/§5) — populated by the
worker, read by dataplane/repositories/postgres/frequency_repository.py,
collocation_repository.py, and ngram_repository.py once they swap onto
these tables instead of scanning raw Token rows at query time.

Both tasks fully replace (delete-then-insert) a document's rows rather than
incrementally upserting — see main/models.py::DocumentNgram's docstring on
why word_type_ids is a plain JSONField instead of the array-typed unique
key the architecture plan originally described: a JSONField can't reliably
back a database-level uniqueness constraint across both Postgres and
SQLite, so idempotency comes from delete-then-insert instead of
ON CONFLICT.
"""

from collections import Counter

from django.db.models import Count

from main.models import DocumentNgram, DocumentWordFreq, Token
from worker.celery_app import app

# The worker decides K and the floor, not the schema (architecture plan
# §2) — storing every 5-gram is storing the corpus again at 5x
# multiplicity, and the combinatorial tail is never read. Generous enough
# that the corpus sizes this project actually has today are never
# truncated in practice; the point is that the MECHANISM exists, ready for
# a real floor/K tuning pass once corpus sizes actually demand one.
TOP_K_PER_DOCUMENT = 500
FREQUENCY_FLOOR = 1

NGRAM_SIZES = (2, 3, 4, 5)


@app.task
def compute_document_word_freq(document_id: int) -> int:
    """COMPLETE per-document word counts — no top-K here, vocabulary size
    is bounded (see DocumentWordFreq's docstring in main/models.py)."""
    DocumentWordFreq.objects.filter(document_id=document_id).delete()

    rows = []
    for mode in (Token.ORIGINAL, Token.CORRECTED):
        counts = (
            Token.objects.filter(document_id=document_id, mode=mode)
            .values("word_type_id")
            .annotate(count=Count("id"))
        )
        rows.extend(
            DocumentWordFreq(document_id=document_id, word_type_id=row["word_type_id"], mode=mode, count=row["count"])
            for row in counts
        )

    DocumentWordFreq.objects.bulk_create(rows, batch_size=5000)
    return len(rows)


@app.task
def compute_document_ngrams(document_id: int) -> int:
    """TOP-K per (document, n, mode) — see this module's TOP_K_PER_DOCUMENT/
    FREQUENCY_FLOOR. Windows never cross a document boundary (each mode's
    word_type_id sequence is pulled and windowed independently per
    document, matching main/views.py::_get_word_lists' same rule for the
    live-parse path)."""
    DocumentNgram.objects.filter(document_id=document_id).delete()

    rows = []
    for mode in (Token.ORIGINAL, Token.CORRECTED):
        word_type_ids = list(
            Token.objects.filter(document_id=document_id, mode=mode).order_by("position").values_list("word_type_id", flat=True)
        )

        for n in NGRAM_SIZES:
            if len(word_type_ids) < n:
                continue

            counter = Counter(zip(*(word_type_ids[i:] for i in range(n))))
            top = [(ids, count) for ids, count in counter.most_common(TOP_K_PER_DOCUMENT) if count >= FREQUENCY_FLOOR]

            rows.extend(
                DocumentNgram(document_id=document_id, n=n, word_type_ids=list(ids), mode=mode, count=count)
                for ids, count in top
            )

    DocumentNgram.objects.bulk_create(rows, batch_size=5000)
    return len(rows)
