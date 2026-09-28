# Content-hash deduplication for document uploads

## Problem

Every upload path unconditionally creates a new `Document` row, even when the exact same file content already exists on the server. Concretely, there are three places this happens today, all following the same shape — decode the file, build an unsaved `Document`, save it, dispatch async indexing:

- `main/views.py::_document_from_upload` (259–268), used by `corpus_create` (271–305), `corpus_edit` (335–372), and `upload_document` (763–798).
- `main/api_documents.py::_document_from_upload` (22–27), used by `DocumentUploadView.post` (75–110).
- `main/api_corpus.py::_document_from_upload` (46–52), used by `CorpusListView.post` (156–177) and `CorpusDetailView.patch` (196–222).

These three are deliberate near-duplicates of each other — `api_documents.py`/`api_corpus.py`'s own module docstrings explain the API modules stay decoupled from `views.py`'s underscore-prefixed internals rather than importing them, treating a JSON API and a server-rendered HTML view as different, additive surfaces that happen to need the same shape.

Re-uploading identical content today creates a second `Document` row, which:
- Wastes storage and re-runs tokenization/indexing for text already indexed once.
- Skews every frequency/collocation/KWIC count in the FastAPI dataplane (`dataplane/`), since it reads the same `main_document`/`main_token`/`main_documentwordfreq` tables Django writes — two rows with identical content each contribute their own counts, with nothing currently deduplicating them.

## Why the existing hash fields don't already solve this

`Document` already has `content_hash`/`tokenized_hash`/`tokenizer_version` fields, added in `main/migrations/0007_tier1_tier2_schema.py` (2026-09-23) as, per `main/models.py`'s own comment on them, "integrity/reproducibility placeholders... reserved now... content_hash/tokenized_hash are computed by the worker but nothing yet checks them for staleness." They're populated by `worker/tasks/indexing.py::index_document` (line 41):

```python
content_hash = hashlib.sha256(document.content.encode("utf-8")).hexdigest()
...
document.content_hash = content_hash
document.tokenized_hash = content_hash
document.tokenizer_version = TOKENIZER_VERSION
```

This runs **asynchronously via Celery**, dispatched with `index_document.delay(doc.id)` immediately after each of the three save sites above — i.e. strictly *after* the `Document` row already exists. By the time a hash value exists to check against, the duplicate row has already been created. Nothing today ever queries `content_hash` for an existing match before creating a new row, and the column has no uniqueness constraint.

This document describes closing that gap: computing the hash synchronously, before the row is created, and using it as a real lookup key.

## Hashing strategy (decisions, with rejected alternatives)

**Algorithm: SHA-256, unchanged.** Already used for `content_hash`/`tokenized_hash`; fits the existing `CharField(max_length=64)` hex-digest column exactly; no realistic collision risk at this document count. Rejected: MD5/SHA-1 (weaker, no upside); BLAKE3/xxHash (solves a throughput problem this app doesn't have — hashing runs once per upload on essay-sized text, not a hot path).

**What to hash: canonicalized decoded text, not raw upload bytes, not the unnormalized decoded string.** Two exports of "the same" document routinely differ in ways that are invisible to a reader but fatal to exact-byte hashing: a leading BOM, CRLF vs. LF line endings, incidental trailing whitespace, and — for the corpus XML format — attribute order on `<segment>` elements. Hashing raw bytes or raw decoded text would mean re-uploading a file that only differs by one of these gets treated as new content, defeating the entire point of the feature for the most common real-world case (the same file, re-exported or re-saved by a different tool).

The canonicalization applied before hashing:
1. Strip a leading UTF-8 BOM (`﻿`).
2. Normalize line endings: `\r\n` and lone `\r` → `\n`.
3. Strip leading/trailing whitespace of the document as a whole.
4. If the content parses as corpus XML with a `<body>` element — the exact shape `main/corpus_parsing.py::parse_document` already detects to decide between structured and flat tokenization — additionally canonicalize via `xml.etree.ElementTree.canonicalize()`, which normalizes attribute order and insignificant whitespace in a standards-defined way (C14N).

Deliberately **not** done: collapsing or normalizing *internal* whitespace, case-folding, or any other content-level normalization. `Document.content` continues to store the exact text as uploaded, unmodified — canonicalization only transforms the hash *input*, never what's persisted or displayed. Over-canonicalizing risks the opposite failure mode: two meaningfully different plain-text documents colliding because whitespace/case differences got normalized away. Near-duplicate detection (the same essay with one typo fixed, a shared prompt paragraph reused across submissions) is a different problem — shingling/MinHash, not hashing — and is out of scope here; the ask is exact-content matching ("by HASH").

**Where computed: synchronously, server-side, at upload time — not client-side, not deferred to the async task.** All three upload code paths already decode the file synchronously in the request (that's what `_document_from_upload` does today); computing the hash at that same point is nearly free and is the only point in the request lifecycle that can prevent a duplicate row from being created at all.

Rejected: hashing client-side (in the KUIS-FE browser, via `crypto.subtle`) before upload. This would require reimplementing the exact same canonicalization logic in JavaScript, with a real risk of the two implementations quietly drifting apart over time (different Unicode normalization behavior, different XML canonicalization semantics — the browser has no built-in C14N). It also wouldn't actually save anything: the server can never trust a client-supplied hash for a uniqueness decision (a malicious or buggy client could send a hash that doesn't match its payload), so the server would need to recompute and verify it anyway, making the client-side computation pure overhead. It would only be worth revisiting if upload payloads become large enough that skipping the transfer of already-known-duplicate bytes matters — not the case here (uploads are essay/transcript-sized text, per `docs/optimisation_plan.md` §6's own framing of expected document scale).

Rejected: continuing to hash only in the async `index_document` task. That's the status quo, and it's exactly the "too late" problem this feature exists to fix.

**Uniqueness enforcement: a DB-level unique constraint on `content_hash`, resolved via `Document.objects.get_or_create()` — not a hand-rolled check-then-create.** A plain `Document.objects.filter(content_hash=hash).first()` followed by a conditional `.create()` has a race window: two concurrent requests uploading the same brand-new content could both pass the `filter()` check before either commits, producing two rows with the same hash. Django's `get_or_create()` already implements the correct fix for exactly this — it attempts `get()`, and on failure creates inside `transaction.atomic()`, catching `IntegrityError` and re-fetching if a concurrent insert won the race — so it's line-for-line what a hand-rolled version would otherwise need to reproduce. This only becomes fully race-safe once the DB constraint is actually applied (see Rollout order below); until then, `get_or_create()` still functions correctly for the non-concurrent case, it just can't rely on the database to reject a true simultaneous race.

Both SQLite (used by the Django test suite) and Postgres (production) treat multiple `NULL` values as mutually non-colliding under a unique constraint, so existing rows without a hash — anything uploaded before this feature ships and not yet backfilled — are unaffected by adding the constraint.

Rejected: an application-level lock (e.g. a Redis mutex keyed by hash) — unnecessary new infrastructure for a problem a native DB unique index already solves.

**Scope of dedup: one shared `Document` row is reused as-is — no ownership/blob model split.** `docs/optimisation_plan.md` §5 (written before this feature was scoped) floats a further idea: splitting `Document` into a content-addressed `TextBlob` (hash + content + Token rows) referenced by possibly-many `Document` shells (title + uploader + corpus memberships), so two uploaders can keep visually distinct Documents while sharing one token index. That solves a different requirement — "let two people who uploaded identical text keep separate records" — than what's being built here, which is explicitly "don't create a second record at all, just reuse the first one in the new corpus." Building the `TextBlob` split is a larger, separate refactor and is not needed for this ask.

Consequence of not doing the split: on a dedup hit, the *existing* document's `title` and `user` (uploader) are left untouched — the second uploader's title choice is discarded, and they aren't credited in their own per-user upload count (`Document.objects.filter(user=request.user).count()`, used by the profile page) for a file they didn't originate. This is an accepted, minor trade-off consistent with "just include it in the corpus" being the literal ask; it can be revisited later if per-uploader attribution turns out to matter more than expected (see Deferred, below).

## Rollout order — do not apply the migration before the backfill reports clean

Because `content_hash` today is only populated by the *old*, non-canonicalized formula (raw `document.content.encode("utf-8")`, no BOM/CRLF/XML normalization), switching to the canonicalized formula changes what hash existing rows *should* have. Two consequences:

1. Until existing rows are recomputed, a fresh upload's canonicalized hash may fail to match an old row that is in fact identical content — a **safe failure mode** (a missed dedup opportunity, not an incorrect match).
2. Applying a unique constraint on `content_hash` before confirming no two existing rows would collide under the *new* formula risks the migration failing outright, if any pre-existing accidental duplicates already exist in the data (plausible, since nothing has ever prevented them).

The required order:

1. Ship the code (shared hashing module, model change with the constraint declared but **its migration not yet applied**, the four call sites wired up, the worker task updated) — dedup is functionally live for new uploads at this point, just not yet DB-enforced against a true concurrent race.
2. Run `manage.py backfill_content_hash` against the target database. It recomputes `content_hash`/`tokenized_hash` for every existing `Document` under the new formula, and **reports, without merging**, any group of documents whose new hash collides (never auto-merges — see Deferred).
3. Resolve any reported collisions by hand, and re-run step 2 until it reports zero collision groups.
4. Only then run `manage.py migrate` to apply the unique-constraint migration.

## Implementation outline

- **New `main/document_ingest.py`** — the single place `content_hash` is computed. Exposes `canonicalize_for_hash(content: str) -> str`, `compute_content_hash(content: str) -> str`, and `get_or_create_document(*, title, content, user) -> tuple[Document, bool]`. Imported by all three upload call-site modules and by `worker/tasks/indexing.py`, replacing that task's inline `hashlib.sha256(...)` call — so the formula is defined once and the upload-time check and the post-index value can never drift apart. New/neutral module rather than added to `views.py`, for the same reason the three `_document_from_upload` copies already avoid cross-importing: `api_documents.py`/`api_corpus.py` deliberately don't reach into `views.py`'s private helpers.
- **`main/corpus_parsing.py`** — extract the "is this corpus XML with a `<body>`" check already implicit in `parse_document` into a named `looks_like_structured_xml(content) -> bool`, so the hash canonicalization's XML branch and tokenization's structured/flat branch use the identical predicate rather than two implementations that could disagree.
- **`main/models.py`** — add `Meta.constraints = [models.UniqueConstraint(fields=['content_hash'], name='uniq_document_content_hash')]` on `Document`, following the naming convention `DocumentWordFreq.Meta.constraints` (`uniq_doc_wordfreq`) already established in this codebase, rather than a bare `unique=True` field kwarg.
- **`main/migrations/0010_document_content_hash_unique.py`** — `AddConstraint` for the above (next migration number after `0009_token_word_type_required.py`).
- **`main/management/commands/backfill_content_hash.py`** — modeled on `backfill_tier2.py`'s "recompute-only" pattern (not `retokenize.py`'s full Token/aggregate rebuild — nothing about tokenization changes here, only the hash formula). Idempotent; safe to re-run.
- **The four call sites** (`corpus_create`, `corpus_edit`, `upload_document`, `DocumentUploadView.post`, `CorpusListView.post`, `CorpusDetailView.patch`) each drop their private `_document_from_upload` and call `get_or_create_document(...)` instead of building-and-saving a `Document` directly, dispatching `index_document.delay(doc.id)` only when a new row was actually created. Resolved documents — new or reused — flow into the existing `corpus.documents.set()` calls exactly as today, reusing the app's already-working attach mechanism (the same one `main/views.py::corpus_assign` and `CorpusAssignView` use).
- **Response/UX**: the standalone upload endpoints (`DocumentUploadView`, `upload_document`) surface a `duplicate` flag / distinct flash message and a `200` instead of `201` status on a dedup hit, since their entire purpose is "did this add something new." The corpus-bundle endpoints surface a `deduplicated_count` instead, since most real uploads go through that path.

## Explicitly deferred

- **`TextBlob`/`Document` ownership split** (`optimisation_plan.md` §5's fuller suggestion) — solves "keep distinct Documents while sharing tokens," which is the opposite of what's being built here (one shared row). Worth revisiting only if per-uploader attribution for shared content becomes a real requirement.
- **Near-duplicate detection** (MinHash/shingling for typo-fixed or partially-copied resubmissions) — a different technique than hashing; this feature is exact-content matching only, by design.
- **Retroactive merge of pre-existing accidental duplicate `Document` rows** already in the database. `backfill_content_hash` *reports* these (so an operator can see the scope of the problem) but never merges them automatically — deciding which title/uploader survives and reassigning corpus memberships is a separate, higher-stakes feature that should be its own explicit request if it turns out to matter in practice.
