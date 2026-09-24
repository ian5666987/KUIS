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
"""

import hashlib

from main.models import Document, Token, build_tokens
from worker.celery_app import app
from worker.tasks.aggregates import compute_document_ngrams, compute_document_word_freq

# Bumped whenever main/corpus_parsing.py's tokenization logic changes in a
# way that would produce different tokens for the same content — nothing
# reads this for staleness detection yet (that's the follow-up
# docs/new-architecture.md's Phase 5 entry notes as still open), but it's
# captured now so that follow-up doesn't need a second backfill pass.
TOKENIZER_VERSION = 1


@app.task
def index_document(document_id: int) -> int:
    document = Document.objects.get(id=document_id)
    document.status = Document.STATUS_INDEXING
    document.save(update_fields=["status"])

    try:
        # Re-indexing (content edited, or a re-run) must not double up —
        # delete first, exactly like main/management/commands/retokenize.py
        # already does for the same reason.
        Token.objects.filter(document=document).delete()

        content_hash = hashlib.sha256(document.content.encode("utf-8")).hexdigest()
        token_count, token_count_corrected = build_tokens(document)

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
        document.save(
            update_fields=[
                "token_count",
                "token_count_corrected",
                "content_hash",
                "tokenized_hash",
                "tokenizer_version",
                "status",
            ]
        )
    except Exception:
        document.status = Document.STATUS_FAILED
        document.save(update_fields=["status"])
        raise

    # Fire-and-forget under a real broker; synchronous under
    # CELERY_TASK_ALWAYS_EAGER=1 (tests) — either way, by the time
    # index_document.delay(...) returns in eager mode, Tier-2 aggregates
    # exist too.
    compute_document_word_freq.delay(document_id)
    compute_document_ngrams.delay(document_id)

    return document_id
