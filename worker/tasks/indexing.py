"""
The async replacement for main/models.py's old post_save signal
(architecture plan §5, optimisation_plan.md Tier 3: "Replace the post_save
signal with an explicit index_document() call"). Triggered explicitly from
main/views.py's corpus_create/corpus_edit/upload_document via
`index_document.delay(doc.id)` right after doc.save() — the signal itself
is gone (see main/models.py's NOTE at the bottom of that file).

Chains into the two aggregate tasks (worker/tasks/aggregates.py) so
Document.status only reaches 'ready' once tokens AND Tier-2 aggregates both
exist — a caller polling status sees either 'indexing' or a fully-usable
'ready' document, never a half-finished state.

This task is the ONLY writer of Document.status/status_reason/status_detail.
A document that never reaches it stays at the model's 'indexing' default
forever, which is the truthful reading of "the worker hasn't touched this" —
and the reason that default is no longer 'ready' (see main/models.py).
"""

from main.corpus_parsing import (
    REASON_MESSAGES,
    REASON_NO_TOKENS,
    REASON_WORKER_ERROR,
    diagnose_content,
)
from main.document_ingest import compute_content_hash
from main.models import Document, Token, build_tokens
from worker.celery_app import app
from worker.tasks.aggregates import compute_document_ngrams, compute_document_word_freq
from worker.tasks.error_annotations import extract_document_error_annotations

# Bumped whenever main/corpus_parsing.py's tokenization logic changes in a
# way that would produce different tokens for the same content — nothing
# reads this for staleness detection yet (that's the follow-up
# docs/new-architecture.md's Phase 5 entry notes as still open), but it's
# captured now so that follow-up doesn't need a second backfill pass.
TOKENIZER_VERSION = 1


def _mark_failed(document: Document, reason: str, detail: str) -> None:
    """Records WHY a document couldn't be indexed, alongside the token counts
    the failure leaves behind (zero — the Token rows were deleted at the top of
    index_document and nothing replaced them). Writing the counts here matters:
    a document that previously indexed fine and now fails must not keep
    advertising its old counts next to a Failed badge."""
    document.status = Document.STATUS_FAILED
    document.status_reason = reason
    document.status_detail = detail
    document.token_count = 0
    document.token_count_corrected = 0
    document.save(
        update_fields=[
            "status",
            "status_reason",
            "status_detail",
            "token_count",
            "token_count_corrected",
        ]
    )


@app.task
def index_document(document_id: int) -> int:
    document = Document.objects.get(id=document_id)
    document.status = Document.STATUS_INDEXING
    document.status_reason = ""
    document.status_detail = ""
    document.save(update_fields=["status", "status_reason", "status_detail"])

    try:
        # Re-indexing (content edited, or a re-run) must not double up —
        # delete first, exactly like main/management/commands/retokenize.py
        # already does for the same reason.
        Token.objects.filter(document=document).delete()

        # Structural check BEFORE tokenizing. main/corpus_parsing.py's
        # parse_document falls back to flat tokenization for anything it can't
        # parse as corpus XML, which silently turns a malformed export's own
        # markup into "words" — a healthy-looking token count over nonsense.
        # Failing here is what makes that case visible instead.
        reason, detail = diagnose_content(document.content, document.title)
        if reason:
            _mark_failed(document, reason, detail)
            return document_id

        content_hash = compute_content_hash(document.content)
        token_count, token_count_corrected = build_tokens(document)

        # Parsed cleanly and still produced nothing — e.g. a <body> containing
        # only whitespace or only empty elements. Not a crash, but not a
        # usable document either, and a 0-token 'ready' document is precisely
        # the state this whole status/reason mechanism exists to surface.
        if token_count == 0:
            _mark_failed(document, REASON_NO_TOKENS, REASON_MESSAGES[REASON_NO_TOKENS])
            return document_id

        document.token_count = token_count
        document.token_count_corrected = token_count_corrected
        document.content_hash = content_hash
        # Identical to content_hash right now (tokenization just ran
        # against this exact content) — kept as a separate field because
        # the two are meant to diverge later: content_hash tracks the
        # document's current content, tokenized_hash tracks what the
        # existing Token rows were actually built from. A future
        # `tokenized_hash IS DISTINCT FROM content_hash` check (not
        # implemented yet) is what would detect a document edited without
        # reindexing — see optimisation_plan.md §5.
        document.tokenized_hash = content_hash
        document.tokenizer_version = TOKENIZER_VERSION
        document.status = Document.STATUS_READY
        document.status_reason = ""
        document.status_detail = ""
        document.save(
            update_fields=[
                "token_count",
                "token_count_corrected",
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
        _mark_failed(
            document,
            REASON_WORKER_ERROR,
            f"{REASON_MESSAGES[REASON_WORKER_ERROR]} ({type(error).__name__}: {error})",
        )
        raise

    # Fire-and-forget under a real broker; synchronous under
    # CELERY_TASK_ALWAYS_EAGER=1 (tests) — either way, by the time
    # index_document.delay(...) returns in eager mode, Tier-2 aggregates
    # exist too.
    compute_document_word_freq.delay(document_id)
    compute_document_ngrams.delay(document_id)
    # Error annotations are a separate concern from tokens/word-freq/ngrams
    # (docs/error-analytics-plan.md) — status above is intentionally NOT
    # gated on this completing; it has its own eventual-consistency window.
    # compute_document_error_freq is chained from inside this task, not
    # fired independently here, so it never races ahead of the annotations
    # it reads.
    extract_document_error_annotations.delay(document_id)

    return document_id
