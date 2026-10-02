# Content-hash deduplication for document uploads

**Status: ✅ Shipped.** Migration `0010` (the unique constraint) is applied; all three upload paths use it.

## What this is

Every upload path used to unconditionally create a new `Document` row, even for byte-identical content re-uploaded under a different filename — wasting storage, re-running tokenization, and double-counting in every FastAPI frequency/collocation/KWIC query (which reads the same tables Django writes). Now, re-uploading identical content reuses the existing `Document` instead of creating a duplicate.

`Document.content_hash`/`tokenized_hash` already existed as unused "integrity placeholder" fields before this — populated asynchronously by the indexing worker, but nothing ever checked them before a row was already created. This feature makes the hash the actual dedup key, computed *before* the row exists.

## Key decisions

- **Hash canonicalized text, not raw bytes**: strip a leading BOM, normalize CRLF→LF, trim whitespace, and (for corpus XML) apply `ElementTree.canonicalize()` to normalize attribute order — so two exports of "the same" document still dedup despite incidental formatting differences. Deliberately narrow: internal whitespace/case are left alone, so meaningfully different plain text never collides. Only the hash *input* is canonicalized — `Document.content` always stores the exact raw upload.
- **Computed synchronously at upload time**, not in the async indexing task — the only point that can actually prevent a duplicate row from being created. Server-side only (not client-side): the server would have to verify a client-supplied hash anyway, making client-side computation pure overhead.
- **Race-safe via a DB unique constraint** on `content_hash`, resolved with `Document.objects.get_or_create()` rather than a hand-rolled check-then-create (which has a TOCTOU race for two simultaneous uploads of new content).
- **One shared row, no ownership split**: a dedup hit reuses the existing document as-is; its title/uploader are never overwritten by the second uploader. (A `TextBlob`/`Document` split for per-uploader attribution was considered and explicitly deferred — different problem, larger refactor.)
- **Rollout order mattered**: the old hash formula (raw bytes, no canonicalization) meant existing rows had to be recomputed via `backfill_content_hash` — reported clean of collisions — *before* the unique constraint migration could safely apply. Both steps are done.

## Where it lives

`main/document_ingest.py` (`canonicalize_for_hash`, `compute_content_hash`, `get_or_create_document` — the one place the formula is defined, imported by every upload site and by `worker/tasks/indexing.py` so upload-time and post-index hashes can never drift apart), `main/corpus_parsing.py::looks_like_structured_xml` (shared XML-detection predicate), `main/migrations/0010_document_content_hash_unique.py`, `main/management/commands/backfill_content_hash.py`.

Call sites: `main/views.py` (`corpus_create`, `corpus_edit`, `upload_document`), `main/api_documents.py::DocumentUploadView`, `main/api_corpus.py` (`CorpusListView`, `CorpusDetailView`) — all call `get_or_create_document()` instead of building a `Document` directly, and skip `index_document.delay()` when the document was reused rather than created.

## Explicitly deferred

- **`TextBlob`/`Document` split** for per-uploader attribution on shared content — only worth building if that attribution turns out to matter in practice.
- **Near-duplicate detection** (typo-fixed resubmissions, MinHash/shingling) — a different technique; this is exact-content matching only.
- **Retroactive merge of pre-existing duplicate rows** — `backfill_content_hash` reports collision groups but never auto-merges them.
