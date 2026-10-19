"""
The async replacement for main/models.py's old post_save signal
(architecture plan §5, docs/data-processing.md Tier 3: "Replace the post_save
signal with an explicit index_document() call"). Triggered explicitly from
main/views.py's corpus_create/corpus_edit/upload_document via
`index_document.delay(doc.id)` right after doc.save() — the signal itself
is gone (see main/models.py's NOTE at the bottom of that file).

Indexing is two tasks, and each advances Document.status one step:

    index_document      indexing -> tokenized   (or failed + a REASON_* code)
    aggregate_document  tokenized -> ready      (or stays tokenized +
                                                 REASON_AGGREGATE_ERROR)

The split is what `tokenized` exists to expose. KWIC reads only Token rows, so
a tokenized document is already searchable there; every other analysis feature
reads an aggregate or the error tables, and dataplane/repositories/shared.py::
resolve_document_ids admits a document to those only once it is `ready`. And
because aggregate_document is queued BEHIND every index_document already in
the queue, a batch upload becomes searchable in KWIC file by file, without
waiting on the whole batch's aggregates.

`ready` means every derived table exists: DocumentWordFreq, DocumentNgram,
ErrorAnnotation and DocumentErrorFreq, built by one task in one transaction
together with the status flip — never a document with n-grams but no word
frequencies, and never a `ready` badge in front of aggregates still being
written.

These two tasks are the ONLY writers of Document.status/status_reason/
status_detail. A document that never reaches index_document stays at the
model's 'indexing' default forever, which is the truthful reading of "the
worker hasn't touched this" — and the reason that default is no longer
'ready' (see main/models.py). One that reaches tokenized but never gets its
aggregate_document (lost message, worker killed) stays 'tokenized', equally
truthfully; `manage.py aggregate_documents` finishes it.

Whether a document's Token rows still match its content and the current
tokenizer is worker/tasks/maintenance.py's question, answered from the
tokenized_hash/tokenizer_version index_document writes here.
"""

from django.db import transaction

from main.corpus_parsing import (
    REASON_AGGREGATE_ERROR,
    REASON_MESSAGES,
    REASON_NO_TOKENS,
    REASON_WORKER_ERROR,
    diagnose_content,
)
from main.document_ingest import compute_content_hash
from main.models import Document, Token, build_tokens
from worker.celery_app import app
from worker.tasks.aggregates import compute_document_ngrams, compute_document_word_freq
from worker.tasks.error_annotations import compute_document_error_freq, extract_document_error_annotations

# Bumped whenever main/corpus_parsing.py's tokenization logic changes in a
# way that would produce different tokens for the same content. Bumping it is
# what makes worker/tasks/maintenance.py::find_stale_documents report every
# document indexed before the change as stale (tokenizer_outdated), so
# `manage.py retokenize` rebuilds exactly those and nothing else.
TOKENIZER_VERSION = 1


def _mark_failed(document: Document, reason: str, detail: str, *, failed_on_hash: str | None = None) -> None:
    """Records WHY a document couldn't be indexed, alongside the token counts
    the failure leaves behind (zero — this run deleted the Token rows and
    nothing replaced them). Writing the counts here matters: a document that
    previously indexed fine and now fails must not keep advertising its old
    counts next to a Failed badge.

    failed_on_hash is for a failure that is a verdict on the content itself —
    malformed XML, no <body>, no words — as opposed to a crash. It is recorded
    as tokenized_hash, with the tokenizer version, because the outcome for that
    exact content is now known: re-running gives the same answer until the
    content or the tokenizer changes, so the staleness check
    (worker/tasks/maintenance.py) leaves the document alone until one of them
    does. A crash says nothing about the content and passes nothing, leaving
    tokenized_hash empty so the check retries it."""
    document.status = Document.STATUS_FAILED
    document.status_reason = reason
    document.status_detail = detail
    document.token_count = 0
    update_fields = ["status", "status_reason", "status_detail", "token_count"]
    if failed_on_hash is not None:
        document.content_hash = failed_on_hash
        document.tokenized_hash = failed_on_hash
        document.tokenizer_version = TOKENIZER_VERSION
        update_fields += ["content_hash", "tokenized_hash", "tokenizer_version"]
    document.save(update_fields=update_fields)


@app.task
def index_document(document_id: int) -> int:
    document = Document.objects.get(id=document_id)
    document.status = Document.STATUS_INDEXING
    document.status_reason = ""
    document.status_detail = ""
    # tokenized_hash/tokenizer_version describe the current Token rows, and
    # this run is about to replace them — so they're cleared now, in the same
    # write that marks the document indexing. A run that never finishes (a
    # killed worker, a dropped connection) then leaves a document the
    # staleness check reports as untokenized, rather than one still vouching
    # for rows this run had started to replace.
    document.tokenized_hash = None
    document.tokenizer_version = None
    document.save(update_fields=["status", "status_reason", "status_detail", "tokenized_hash", "tokenizer_version"])

    try:
        with transaction.atomic():
            # Row lock held across the whole delete-and-rebuild. Without it,
            # two runs for the same document (a retokenize alongside the
            # worker, two sweeps queuing it before either ran) interleave their
            # deletes and inserts on a multi-process worker and leave every
            # token doubled; with it, the second waits and then redoes the
            # work. Re-read under the lock so the content tokenized is the
            # content as it is now. (SQLite has no row locks, but its
            # database-level write lock keeps two writers apart anyway.)
            document = Document.objects.select_for_update().get(id=document_id)

            # Re-indexing (content edited, or a re-run) must not double up —
            # delete first.
            Token.objects.filter(document=document).delete()

            content_hash = compute_content_hash(document.content)

            # Structural check BEFORE tokenizing. main/corpus_parsing.py's
            # extract_words falls back to flat tokenization for anything it can't
            # parse as corpus XML, which silently turns a malformed export's own
            # markup into "words" — a healthy-looking token count over nonsense.
            # Failing here is what makes that case visible instead.
            reason, detail = diagnose_content(document.content, document.title)
            if reason:
                _mark_failed(document, reason, detail, failed_on_hash=content_hash)
                return document_id

            token_count = build_tokens(document)

            # Parsed cleanly and still produced nothing — e.g. a <body> containing
            # only whitespace or only empty elements. Not a crash, but not a
            # usable document either, and a 0-token 'ready' document is precisely
            # the state this whole status/reason mechanism exists to surface.
            if token_count == 0:
                _mark_failed(
                    document, REASON_NO_TOKENS, REASON_MESSAGES[REASON_NO_TOKENS], failed_on_hash=content_hash
                )
                return document_id

            document.token_count = token_count
            document.content_hash = content_hash
            # Identical to content_hash at this moment (tokenization just ran
            # against this exact content), but the two diverge the moment
            # `content` is edited anywhere other than an upload: tokenized_hash
            # keeps saying what the Token rows were built from, which is what
            # worker/tasks/maintenance.py compares a fresh hash of the content
            # against.
            document.tokenized_hash = content_hash
            document.tokenizer_version = TOKENIZER_VERSION
            document.status = Document.STATUS_TOKENIZED
            document.status_reason = ""
            document.status_detail = ""
            document.save(
                update_fields=[
                    "token_count",
                    "content_hash",
                    "tokenized_hash",
                    "tokenizer_version",
                    "status",
                    "status_reason",
                    "status_detail",
                ]
            )
    except Exception as error:
        # Keep the exception's own text: an unexpected failure is the one case
        # where there is no pre-written explanation, and the type/message is
        # what an operator needs to act on. The raise is preserved so Celery
        # still records the task as failed with its traceback.
        with transaction.atomic():
            # The rollback above put back whatever Token rows the document had
            # before this run. A failed document holds none, so remove them
            # here, as the content-failure paths above already have.
            Token.objects.filter(document_id=document_id).delete()
            _mark_failed(
                document,
                REASON_WORKER_ERROR,
                f"{REASON_MESSAGES[REASON_WORKER_ERROR]} ({type(error).__name__}: {error})",
            )
        raise

    # Fire-and-forget under a real broker; synchronous under
    # CELERY_TASK_ALWAYS_EAGER=1 (tests) — either way, by the time
    # index_document.delay(...) returns in eager mode, the document is ready.
    aggregate_document.delay(document_id)

    return document_id


@app.task
def aggregate_document(document_id: int) -> int:
    """tokenized -> ready. Builds every derived table the analysis features
    read besides Token itself, then marks the document ready — all in ONE
    transaction, so a reader sees either the previous state or the finished
    one.

    The four builders run in a fixed order, as plain function calls rather than
    separate .delay()s: two unordered .delay() calls give no guarantee the
    error annotations exist before compute_document_error_freq reads them, and
    separately dispatched tasks have no single point at which "all of them
    finished" is known — which is exactly how `ready` used to be set without
    them."""
    document = Document.objects.get(id=document_id)
    if document.status != Document.STATUS_TOKENIZED:
        # Re-indexed since this was queued (back to 'indexing', with its own
        # aggregate_document coming), failed, or a duplicate message for a
        # document that already finished. None of those is this run's to
        # advance.
        return document_id

    try:
        with transaction.atomic():
            compute_document_word_freq(document_id)
            compute_document_ngrams(document_id)
            extract_document_error_annotations(document_id)
            compute_document_error_freq(document_id)

            # Conditional on still being tokenized: if index_document re-ran
            # while the aggregates were being built, the status is back at
            # 'indexing' and these rows are about to be replaced — this run
            # must not be the one that says 'ready'.
            Document.objects.filter(id=document_id, status=Document.STATUS_TOKENIZED).update(
                status=Document.STATUS_READY,
                status_reason="",
                status_detail="",
            )
    except Exception as error:
        # The transaction rolled back, so no partial aggregates were left
        # behind. The tokens are still good, so the document stays tokenized
        # (still in KWIC) rather than failing outright — status_reason says
        # why it got no further.
        Document.objects.filter(id=document_id, status=Document.STATUS_TOKENIZED).update(
            status_reason=REASON_AGGREGATE_ERROR,
            status_detail=f"{REASON_MESSAGES[REASON_AGGREGATE_ERROR]} ({type(error).__name__}: {error})",
        )
        raise

    return document_id
