"""Content-hash based upload deduplication (docs/data-processing.md).

main/views.py, main/api_documents.py, main/api_corpus.py and
worker/tasks/indexing.py all import compute_content_hash/get_or_create_document
from here rather than each having their own hashlib call, so the formula can
never drift into two different things that happen to share a name. The formula
itself lives in main/content_hash.py, re-exported here, because Document.save()
needs it too and this module imports the models.

Deliberately a new/neutral module rather than living in main/views.py:
api_documents.py and api_corpus.py's own docstrings explain they don't import
views.py's underscore-prefixed internals (a different, additive surface
reaching into another view module's private helpers). This module belongs to
none of the three upload-site modules, so all three — and the worker — can
import it without that concern applying.
"""

from .content_hash import compute_content_hash
from .metadata_catalogue import link_catalogue_entry
from .models import Document


def decode_uploaded_file(uploaded_file):
    """Reads + UTF-8-decodes an uploaded file. Raises UnicodeDecodeError on
    non-UTF-8 content — callers turn that into a form error / 400 response,
    exactly like the three (now-removed) per-module _document_from_upload
    helpers did."""
    return uploaded_file.read().decode("utf-8")


def get_or_create_document(*, title, content, user):
    """Resolves (title, content) to a Document — reusing an existing one with
    identical content_hash instead of inserting a duplicate row. Returns
    (document, created), mirroring Django's own get_or_create() (which this
    delegates to directly): title/user are only applied when NEW — a dedup
    hit never renames or reassigns the existing document, so an upload under
    a different filename/by a different user can't silently overwrite who
    owns the original.

    Race-safe via the DB's unique constraint on content_hash
    (main/migrations/0010_document_content_hash_unique.py — see that
    migration's own note on why it must be applied only after
    backfill_content_hash reports zero collisions). Two concurrent uploads of
    brand-new identical content: get_or_create()'s own IntegrityError-catch-
    and-refetch handles the loser.

    Never touches corpus membership and never dispatches indexing — that's
    the caller's job (skip index_document.delay() when created is False; a
    reused document is already indexed).

    Does do one local thing beyond the insert: on a NEW document, links it to
    its metadata catalogue entry (docs/data-processing.md). That's
    synchronous and here, rather than in the worker and rather than repeated
    at all six upload sites, for the same reason content_hash is computed
    here — a document has to be visible to metadata filters the moment it
    exists, and this is the one path every upload already funnels through.
    It's a single indexed lookup, and a miss is not an error (the document
    simply reads as having no metadata). A dedup hit is correctly skipped:
    the existing document was linked when it was first created.
    """
    content_hash = compute_content_hash(content)
    document, created = Document.objects.get_or_create(
        content_hash=content_hash,
        defaults={"title": title, "content": content, "user": user},
    )

    if created:
        link_catalogue_entry(document)

    return document, created
