# KUIS — Data Storage, Processing & Query Handling

> How corpus text gets in, how it is stored, how it becomes analytics values,
> and how a query reaches them. Companion to `docs/feature-list.md` (what the
> features are) and `docs/ONBOARDING.md` (how to run the thing).
> Dated history: `docs/CHANGELOG.md`.

---

## 1. The shape of the problem

A learner corpus is small in bytes and awkward in shape. The source of truth is
XML per file:

```xml
<document>
  <header><textfile>KUIS/KUIS2023FUA201.txt</textfile><lang>indonesian</lang></header>
  <body>
    Plain text ... <segment id='104' features='eror;leksikal;kata;ktinf' Correction='tetapi'>tapi</segment> ...
  </body>
</document>
```

Three things have to come out of that, and they pull in different directions:

1. **Two parallel word streams** — the text as written (`original`) and the text
   with every `Correction` applied (`corrected`). Every analysis feature can be
   run against either.
2. **Positional data** — KWIC and EPIC need to know *where* a word is, with a
   context window, without straddling a document boundary.
3. **Aggregates** — frequency, collocations and n-grams only ever need counts,
   and they need them summed over an arbitrary subset of documents.

Plus the annotations themselves (`<segment>`), which are a third dataset with
their own taxonomy, and two CSV metadata catalogues describing the files and
their writers.

### The format rules worth knowing

- `features` is a **semicolon-separated tag path** (`eror;<category>;<subcategory>;<leaf>`)
  whose depth varies by error type. The taxonomy is in-repo at `info/error.xml`.
- `<header><textfile>` is read by `extract_textfile_name` and is the **primary
  metadata match key** (§6). It deliberately does *not* gate on
  `looks_like_structured_xml`, whose `<body>` requirement is a tokenization
  concern, not a header one.
- `parse_document()` falls back to flat (non-XML-aware) tokenization when the
  content isn't parseable XML or has no `<body>`, and returns an `is_structured`
  flag for exactly that case. The UI names the affected files in corrected mode
  (`components/mode_notice.html`), where the fallback would otherwise look like
  an identical result for no reason. **Unparseable content is never an upload
  failure.**
- **Nested `<segment>` elements are handled.** They were silently dropped by the
  original tokenizer; fixed as part of the error-analytics work.
- **`Correction` vs `correction` attribute casing.** Sample files use both; the
  walker only reads capital-C `Correction`, so lowercase-attribute segments
  contribute nothing to the corrected word stream.

---

## 2. How the data is stored

One PostgreSQL database. Django's `main/migrations/` is the **only** schema
authority; FastAPI mirrors the tables by hand and the worker writes through
Django's ORM.

### Core

- **`Document`** — `title`, `content` (the raw XML/text, exactly as uploaded),
  `uploaded_at`, `created_at`, `user` (FK→`User`, nullable, **`CASCADE`** — the
  reason account deletion is soft), `token_count`, `token_count_corrected`,
  `status` (`ready`|`indexing`|`failed`, **defaults to `indexing`** — a row exists
  before its tokens do; only `worker/tasks/indexing.py` ever moves it),
  `status_reason`/`status_detail` (why a `failed` document failed — a stable
  `REASON_*` code from `main/corpus_parsing.py` plus the sentence to show; both
  empty otherwise), `content_hash` (**unique**), `tokenized_hash`,
  `tokenizer_version`. Index on `(user, uploaded_at)`.
  Above ~2 KB Postgres TOASTs `content` — stored out-of-line and compressed.
- **`Corpus`** — `name` (unique), `description`, `created_by` (FK→`User`,
  `SET_NULL`), `created_at`, `is_public` (`default=True`), and
  **`documents = ManyToManyField(Document, related_name='corpora')`**. The M2M is
  deliberate: a file may belong to several corpora, and the client has accepted
  that overlap.

### Tier 1 — the normalised token index

- **`WordType`** — `form` (unique). One row per distinct word form, project-wide.
- **`Token`** — `document` (FK, `related_name='tokens'`), **`word_type`**
  (FK→`WordType`), `position`, `mode` (`original`|`corrected`). One row per token
  per mode per document.

`Token.word_type` **replaced** an earlier `Token.word varchar(100)` column, and
that change is the single highest-leverage storage decision in the project.
Heaps' law means types grow roughly as √tokens — a 10M-token Indonesian corpus has
perhaps 200–300k types — so storing an int FK instead of a string drops the heap
row from ~64 to ~24 bytes, shrinks the `(word)` index to an index on a small
table, shrinks the large composite index proportionally, and turns
`GROUP BY word` into an integer operation. Roughly 3× smaller and materially
faster aggregates.

Tokenization is **per-document**, which is why corpus membership changes never
require retokenizing anything.

### Tier 2 — the precomputed aggregates

- **`DocumentWordFreq`** — `(document, word_type, mode, count)`.
- **`DocumentNgram`** — `(document, n, word_type_ids, mode, count)`.
  `word_type_ids` is a plain **`JSONField`** holding an ordered `list[int]`, not an
  `ArrayField` — `ArrayField` is Postgres-only and the Django test suite
  deliberately runs on SQLite.

**Both aggregates are per-document grain.** That is the load-bearing property of
the whole read path: narrowing the document id list — a corpus selection, a
metadata filter, anything — just sums over fewer rows. It needed no query rework
anywhere, and an empty id list is already every query builder's "nothing to
compute" guard.

This is the query shape every serious corpus tool uses (Sketch Engine, CQPweb,
IMS Corpus Workbench), and it is what makes multi-corpus selection **additive**
rather than O(corpus).

### Error analytics

- **`ErrorTaxonomyNode`** — `code` (unique), `parent` (self-FK), `path` (unique),
  `gloss`, `is_leaf`, `top_category`. Loaded from `info/error.xml`, ~90 rows. A
  leaf's `path` always ends with its own code, which is what lets
  `concat(';', path, ';') LIKE '%;code;%'` match **both** a direct leaf hit and
  every descendant of a checked category — one mechanism, no separate
  exact-match branch.
- **`ErrorAnnotation`** — one row per `<segment>`: `document`, `taxonomy_node`
  (`SET_NULL`, nullable), `raw_features` (the original string, kept for later
  re-resolution), `parent` (self-FK, resolved from the segment's explicit
  `parent=` attribute, **not** structural nesting — real data shows these
  diverge, and the attribute is authoritative), `source_segment_id`,
  `start_position`, `end_position`, `original_text`, `correction_text`, `state`,
  `comment`.
- **`DocumentErrorFreq`** — `(document, taxonomy_node, count)`, used **only** by
  the summary view. Error Frequency itself queries `ErrorAnnotation` live, since
  phrase text isn't a bounded-enough dimension for an aggregate to help.

### Accounts & metadata

- **`UserProfile`** — a sidecar (`OneToOne`, `related_name='profile'`) carrying
  `blocked_at`/`blocked_by`, `deleted_at`/`deleted_by`. See
  `docs/feature-list.md` §5 for why it isn't a custom `AUTH_USER_MODEL`.
- **`DocumentMetadata`** — the file catalogue; §6.
- **`Author`** / **`AuthorMotherTongue`** — person-level survey data; §7.

### Migration history, briefly

`0001`–`0004` build `Document`/`Token`. `0005` adds `Corpus` + its M2M.
**`0006_legacy_corpus` is a data migration** sweeping every pre-existing document
into a corpus named `"Ungrouped (legacy import)"`, because a document in no
corpus is invisible to every analysis feature (reversible — the reverse deletes
that corpus). `0007`–`0009` are the Tier-1/2 change, **`0008` being the
backfill**. `0010`–`0016` are all pure additions: the `content_hash` unique
constraint, `Corpus.is_public`, the three error-analytics models, `UserProfile`,
`DocumentMetadata`, `Author`+`AuthorMotherTongue`, and the `status_reason`/
`status_detail` columns.

> **All 16 migrations are applied to `corpus_db`**, and both metadata sets are
> loaded there (verified 2026-10-14). Earlier docs said otherwise; that is no
> longer true. The one residue is a stale `Author` row — §7.

### The mirror, and why it is dangerous

`dataplane/models/tables.py` hand-mirrors Django's tables as SQLAlchemy Core
`Table` objects — **not** ORM models, no Alembic. It must be kept in step with
Django's migrations **by hand**, and drift is silent.
`dataplane/tests/test_schema_drift.py` exists to catch it, comparing against
Django's real `_meta` column-for-column, and a new table must be registered in
**all three** places there (import list, `SA_TABLES`, `parametrize`) — one
without the others defeats the guard. This has already bitten once:
`Corpus.is_public` was added by migration `0011` and never mirrored.

---

## 3. The write path: upload → analytics values

**The worker is the only writer of every derived table.** There is no `post_save`
signal — the synchronous one that used to live in `main/models.py` was removed
entirely, and the six upload sites call `index_document.delay(doc.id)`
explicitly.

```
POST (any of six upload sites)
  └─ document_ingest.get_or_create_document()
       ├─ canonicalize content → sha256 → get_or_create on content_hash
       ├─ if CREATED: link_catalogue_entry()  (synchronous — a document must be
       │                                       filterable the moment it exists)
       └─ if CREATED: index_document.delay(doc.id)
            └─ worker/tasks/indexing.py
                 ├─ build_tokens()  → WordType rows + Token rows (both modes)
                 ├─ write token_count, token_count_corrected
                 ├─ write content_hash / tokenized_hash / tokenizer_version
                 ├─ chain → compute_document_word_freq   (DocumentWordFreq)
                 ├─ chain → compute_document_ngrams      (DocumentNgram)
                 └─ chain → extract_document_error_annotations (ErrorAnnotation)
                              └─ calls compute_document_error_freq itself
                 status: indexing → ready   (or failed + a REASON_* code)
```

**Why the chain is shaped that way.** `extract_document_error_annotations` calls
`compute_document_error_freq` **itself** rather than being a second `.delay()`
from `index_document` — two unordered `.delay()` calls give no guarantee the
annotations exist before the aggregate reads them.

> **`status = ready` does not mean the aggregates exist.**
> `worker/tasks/indexing.py`'s own docstring claims it does, but the code sets
> `ready` *before* dispatching `compute_document_word_freq`,
> `compute_document_ngrams` and `extract_document_error_annotations`
> fire-and-forget. **A `ready` document with zero aggregates is a reachable
> state**, and because the three tasks are dispatched separately a document can
> end up with n-grams but no word frequencies. When an analysis feature returns
> empty while KWIC works, suspect this first — frequency reads
> `main_documentwordfreq` while KWIC reads raw `main_token`. Diagnose by
> comparing `Token` against `DocumentWordFreq` row counts per document; repair
> with `backfill_tier2` and `backfill_error_annotations`, which run in-process
> and bypass Celery entirely.

**Idempotence.** `index_document` deletes before inserting, so a re-run never
doubles up; the same is true of the aggregate and annotation tasks. Writes are
wrapped in `transaction.atomic()` with `bulk_create(batch_size=5000)`.

### Ordering constraints that bite

- **`load_error_taxonomy` must run before any upload.**
  `extract_document_error_annotations` resolves `raw_features` against
  `ErrorTaxonomyNode` *at extraction time*; with an empty taxonomy,
  `taxonomy_node` stays NULL (`raw_features` keeps the original string, so
  re-resolution is possible). Fix with `backfill_error_annotations --all`.
- **No worker, no analysis.** With Redis or the worker down, an upload *succeeds*
  and the document sits at 0 tokens, invisible to every analysis feature. Recover
  with `python manage.py retokenize`.
- **The worker must run `--pool=solo`, and only one worker at a time.** The
  default prefork pool is broken in this environment (Python 3.14, celery 5.6.0,
  macOS arm64): forked children never run `setup_worker_optimizations`, so
  `celery/app/trace.py::fast_trace_task` unpacks a still-empty module global and
  **every task dies with `ValueError: not enough values to unpack` before the task
  body executes** — so there is no traceback from project code. Two workers
  sharing one Redis queue is worse than one broken worker: whichever instance
  grabs a message decides whether a document gets its aggregates, leaving
  documents in mixed states.
- **`Token` rows go stale on content edits.** Nothing re-indexes a document whose
  `content` is edited afterwards. `tokenized_hash` exists to detect exactly that
  (`tokenized_hash IS DISTINCT FROM content_hash`) but **that check is not
  implemented** — run `retokenize` by hand. See §9.
- **`CELERY_TASK_ALWAYS_EAGER=1` is required by both test suites.** Fixtures call
  the real `index_document.delay(...)`; eager mode runs it in-process with zero
  broker connections. Without it, 16 Django tests and most data plane tests fail.

---

## 4. Content-hash deduplication

Every upload path used to unconditionally create a new `Document`, even for
byte-identical content re-uploaded under a different filename — wasting storage,
re-running tokenization, and **double-counting in every frequency/collocation/KWIC
query**. The hash is now the actual dedup key, computed *before* the row exists.

- **Hash canonicalized text, not raw bytes.** Strip a leading BOM, normalize
  CRLF→LF, trim surrounding whitespace, and for corpus XML apply
  `ElementTree.canonicalize()` to normalize attribute order — so two exports of
  "the same" document still dedup despite incidental formatting differences.
  Deliberately narrow: internal whitespace and case are left alone, so
  meaningfully different plain text never collides. **Only the hash *input* is
  canonicalized — `Document.content` always stores the exact raw upload.**
- **Computed synchronously at upload time**, not in the async task — the only
  point that can actually prevent a duplicate row from being created. Server-side
  only: the server would have to verify a client-supplied hash anyway.
- **Race-safe via a DB unique constraint**, resolved with `get_or_create()` rather
  than a hand-rolled check-then-create (a TOCTOU race for two simultaneous
  uploads of new content).
- **One shared row, no ownership split.** A dedup hit reuses the existing document
  as-is; `title`/`user` live in `get_or_create`'s `defaults`, so whichever upload
  won owns it, and the second uploader never overwrites it.
- **A dedup hit does not re-link metadata.** The returned document was linked when
  first created. Consequence: if two catalogue files hold byte-identical content,
  only one `Document` exists, only one entry can claim it, and the second stays
  unlinked — reported by the import as awaiting upload.

The formula is defined once, in `main/document_ingest.py`
(`canonicalize_for_hash`, `compute_content_hash`, `get_or_create_document`), and
imported by every upload site **and** by `worker/tasks/indexing.py` so
upload-time and post-index hashes can never drift. Call sites: `main/views.py`
(`corpus_create`, `corpus_edit`, `upload_document`),
`main/api_documents.py::DocumentUploadView`, `main/api_corpus.py`
(`CorpusListView`, `CorpusDetailView`). **A seventh upload path must go through
`get_or_create_document` or it silently skips dedup *and* metadata linking.**

Rollout note, if you meet an old database: the original hash formula used raw
bytes, so existing rows had to be recomputed via `backfill_content_hash` — which
reported clean of collisions — *before* the unique constraint could safely apply.

**Deferred:** a `TextBlob`/`Document` split for per-uploader attribution on shared
content · near-duplicate detection (typo-fixed resubmissions, MinHash/shingling —
a different technique; this is exact-content matching only) · retroactive merge of
pre-existing duplicate rows (`backfill_content_hash` reports collision groups but
never merges them).

> On framing: in a corpus of unique student texts the dedup ratio is near 1.0.
> Justify this work as **integrity and reproducibility**, not a space saving — it
> is idempotent ingest, plus the `tokenized_hash` staleness check, that earns its
> keep. Hashing a corpus as the hash of its sorted member hashes would also give
> a citable corpus version id, which matters more in linguistics than in most
> domains.

---

## 5. The read path: how a query becomes an answer

### The two surfaces diverge sharply

| Feature | Server-rendered Django | FastAPI data plane |
|---|---|---|
| Word frequency | **re-parses `Document.content` live** | sums `DocumentWordFreq` |
| Collocations | **re-parses live** | sums `DocumentNgram` (n=2) |
| N-grams | **re-parses live** | sums `DocumentNgram` |
| KWIC (legacy) | **re-parses live** | `Token` self-join |
| KWIC (fast) | `Token` scan | the same `Token` self-join |
| Error analytics | — (not built here) | `ErrorAnnotation` / `DocumentErrorFreq` |
| Token-count badges | denormalised ints on `Document` | same |

So the Django pages' cost scales with the number of selected files on **every
request** — every sort click, every filter change, every pagination step. The
FastAPI path doesn't. This is the single biggest reason the two surfaces are not
equally fast on large corpora, and retiring the superseded Django views is
pending work (§9).

### The one resolution point

Both surfaces turn "what is selected" into a document id list at **exactly one
place each**:

- `dataplane/repositories/shared.py::resolve_document_ids`
- `main/views.py::_get_selected_documents`

Both already resolved a corpus selection to documents, so adding the metadata
filter there meant **no repository, SQL builder, worker task or analysis view
changed**, and all five Django `/export/` routes inherited it for free. The
per-repository alternative would have touched 9 ABC signatures, 6 repositories
and 6 query builders. **Add the filter anywhere else and you have two places to
keep in step.**

### Shared query builders (data plane)

`repositories/postgres/` holds `_ranked_query`, `_aggregate_query`,
`_ranked_pagination` and `_error_query`. Frequency, collocations and n-grams are
one shared builder at different values of `n`; the ranked-table controls
(filter mode, sort, direction, pagination) are expressed once.

### KWIC's deliberate compromise

`KWICRepository.search()` takes `words: list[str]` — a phrase, not a single word —
which is what lets one repository absorb both the legacy phrase search and the
fast single-word path. A match is an N-word position-shifted self-join over
`Token` (query length is capped at `KWIC_WINDOW_MAX`=10).

- `sort=center` (corpus order, the default) is **fully SQL-paginated**: a separate
  `COUNT(*)`, a real `LIMIT`/`OFFSET`, and context windows for a whole page of
  hits fetched in one batched query via `position BETWEEN`.
- The other three sorts **fetch the full match set** (capped at 10,000), hydrate,
  sort in Python, then slice — mirroring the Django view's own sort-before-paginate
  shape, just against indexed data.
- An unrecognized `sort` value is a **422**, a deliberate divergence from
  `_sort_kwic`'s silent fallback.

KWIC is also the one feature that could not swap onto an aggregate table:
concordance search needs position-level data no aggregate can provide. (It did
need a `word_type` join fix when `Token.word` disappeared.)

### Ranked-table controls

Django's `_rank_counter()` applies the filter (contains/starts/ends/exact), the
sort (word or count, with the count-tie falling back to alphabetical) and the
pagination shared by all three count analyses. Clicking the active sort column
flips direction; a new column starts at its natural direction and returns to page
1. The `/export/` routes run the identical pipeline unpaginated, which is why an
export always matches what was on screen.

---

## 6. The document metadata catalogue

Every analysis feature was scoped on one axis — which corpora. There was no way
to ask "frequency over grade-3 writing only" or "KWIC across TUFS documents",
even though the information existed as two CSV catalogues totalling **1,559 rows
(1,557 distinct filenames)** living outside the database.

`metadata/*.csv` is now a table, each row linked to its uploaded `Document`.
7 universities, Year ∈ {2023, 2024}, Grade ∈ 1–4, ~106 topics, 422 name codes.

```python
class DocumentMetadata(models.Model):
    source_filename = models.CharField(max_length=200)         # CSV "File name" verbatim
    match_key = models.CharField(max_length=200, unique=True)  # basename, one extension off, lowered
    document = models.OneToOneField(
        Document, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='catalogue_entry',
    )
    university = models.CharField(max_length=50, null=True, blank=True)
    year = models.IntegerField(null=True, blank=True)
    grade = models.IntegerField(null=True, blank=True)
    topic = models.CharField(max_length=100, null=True, blank=True)
    topic_en = models.CharField(max_length=100, null=True, blank=True)
    word_count = models.IntegerField(null=True, blank=True)
    name_code = models.CharField(max_length=50, null=True, blank=True)
    source_file = models.CharField(max_length=100)
    imported_at = models.DateTimeField(auto_now=True)
```

Indexes on `university`, `(year, grade)`, `name_code`.

**Why the FK lives on the catalogue side, not on `Document`.** The catalogue is
the authority and must load independently of upload order — all 1,557 entries can
exist before a single file is uploaded, and a file can be uploaded before its row
exists. So resolution runs in **both directions**. It also leaves `main_document`
untouched (nothing to re-mirror in the data plane), and `OneToOneField` enforces
at DB level that two entries can't claim one `Document` — which matters, because
the content-hash constraint collapses byte-identical uploads into a single row
several catalogue filenames could otherwise point at.

**Other decisions.** `SET_NULL` not `CASCADE` — documents are never hard-deleted
today, and the row is source data that should outlive one. `match_key` is stored
rather than derived so the join is an indexed equality. `related_name='catalogue_entry'`,
not `metadata`, which would read as a sibling of Django's `_meta` and SQLAlchemy's
`metadata`. **Every analysis field is nullable** because the "(no value)" filter
needs one representation of empty — a blank cell imports as `None`, never `''`.
`Number of words` is stored but **not filterable**: it is a third, independent
notion of length alongside `token_count`/`token_count_corrected`, which come from
our own tokenizer and legitimately disagree.

### The matching rule

The catalogue describes files by name — `TUFS2023KOMSHI314.txt`. Uploads are
`.xml`, sometimes with a suffix the catalogue has never heard of: the real sample
on disk is `KUIS2023FUA201-Eror.xml` against a catalogue row for
`KUIS2023FUA201.txt`.

A document's **match key** is, in order of preference:

1. the basename of its own `<header><textfile>` — `KUIS/KUIS2023FUA201.txt` →
   `KUIS2023FUA201.txt`;
2. failing that, `Document.title`.

Either way the key is then normalized: basename, strip **exactly one** extension,
lowercase, trim. `KUIS/A2023B.txt`, `A2023B.XML` and `  a2023b.txt  ` all land on
`a2023b`. A catalogue row's `match_key` is the same function applied to its
`File name`, stored at import.

Nothing here is best-effort guessing. **A document either matches exactly one
entry or matches none, and matching none is a fully supported state** — the
document stays usable and reads as having no metadata.

- **The header wins over the filename.** It travels inside the file, so it
  survives renaming, and it is the only thing reconciling the two naming schemes
  actually in use. Matching on it means **no suffix-stripping heuristic exists
  anywhere** — a heuristic would silently mis-link files whose names genuinely
  differ. `A2023B-Eror.xml` normalizes to `a2023b-eror`, *not* `a2023b`; the
  header is what resolves that case, not the normalizer.
- **The title fallback is weaker than it looks.** `Document.title` is the uploaded
  filename only on the four corpus-upload paths. On `upload_document` and
  `DocumentUploadView` it is free text the user typed, so those match only by luck
  — or by their header.
- **A claimed entry is never reassigned, and only a NULL link is ever filled in.**
  Re-importing is idempotent, and two documents mapping to one entry is a data
  question for a human, not something to settle by last-write-wins.
- **A missed match is reported, never raised.** Bad XML, plain text, an empty
  `<textfile>` — all fall through to the title.

Code: `main/metadata_catalogue.py` (`extract_textfile_name`,
`normalize_match_key`, `match_key_for_document`, `link_catalogue_entry`
document→catalogue, `relink_entries` catalogue→document). Called from
`get_or_create_document` (on create only) and `load_metadata_catalogue`.

**Deferred:** fuzzy or near-miss matching · matching on `content_hash` instead of
a filename (the catalogue records no hash, so there is nothing to match against) ·
reconciling byte-identical files under two catalogue names (reported, resolved
manually) · re-linking on a title edit (`--relink` is the manual path).

### Loading it

```bash
python manage.py load_metadata_catalogue              # every metadata/*.csv
python manage.py load_metadata_catalogue --file X.csv
python manage.py load_metadata_catalogue --dry-run    # report only, write nothing
python manage.py load_metadata_catalogue --relink     # re-resolve FKs, read no CSV
```

Idempotent, like `load_error_taxonomy` and `seed`, and for the same reason:
catalogue content gets corrected and extended independently of the schema. Unlike
`load_error_taxonomy` it **upserts** rather than replacing the table, because
these rows carry resolved `document` FKs a delete-and-recreate would discard.

The source data's quirks drive the rest: CRLF endings with no trailing newline
(opened with `newline=''`, which is what stops a stray `\r` on every
`Name code`); a BOM from Excel re-exports (`utf-8-sig`); headers matched case- and
whitespace-insensitively; a non-numeric year/grade/word-count drops that **one
value** to `None` and reports it rather than failing a row whose other fields are
still worth having. `File name` is **not unique** — `OU2023BETA202.txt` and
`OU2023OUSA202.txt` each appear twice with conflicting data — so
`update_or_create` means **last row wins**, and the command names those files and
their line numbers in a `WARNING` block rather than letting row order silently
decide. A byte-identical repeat is reported separately as harmless. Values are
otherwise stored **verbatim**: normalising the inconsistent topic glosses
(`Topic` ↔ `Topic (English)` is not 1:1 — `tokoh` is glossed two ways, "dance"
maps to two Indonesian topics) would hide a data-quality problem that belongs
upstream.

> **`metadata/` holds two different schemas in two directories, and the split is
> load-bearing.** `_resolve_paths` globs `metadata/*.csv` **non-recursively** and
> `_load_one` hard-fails any CSV without a `File name` column — so an author CSV
> sitting in `metadata/` aborts the whole catalogue import before anything is read
> (`KUIS.csv` sorts first). Guarded by
> `main/tests.py::test_only_catalogue_schema_csvs_live_directly_in_metadata`.

**Deferred:** Topic / Topic (English) as facets (stored, imported and indexed, but
~106 and ~108 values need a searchable multi-select; the facet mechanism is
field-agnostic, so adding them is a name in `FACET_FIELDS`, one in
`METADATA_FACETS`, and a UI control) · `Number of words` as a filter · an admin
CSV upload endpoint (`import_catalogue_rows` is already split out from the command
for it, but an upload path needs the `utf-8-sig`/CRLF handling
`decode_uploaded_file` lacks) · facet counts in the picker · normalising the
inconsistent topic glosses and capitalisation.

---

## 7. Author (person-level) metadata

The catalogue answers *about the file* — university, year, grade, topic. It cannot
answer *about the writer*. `metadata/authors/{KUIS,OU,TUFS}.csv` supply that:
20 columns, **one row per person per survey occasion** — a different grain from
the catalogue's one-row-per-file. **437 rows describing 235 people.**

Two traits ship as filter facets: **gender** and **mother tongue**. Grade already
shipped with the catalogue at 100% coverage. **The source's other sixteen columns
are read past during import and dropped.**

### Coverage — know this before trusting a filtered result

| University | Catalogue files | Linked to an author |
|---|---|---|
| TUFS | 527 | 527 (100%) |
| KUIS | 143 | 142 (99%) — one file short, see the stale-row note below |
| OU | 64 | 64 (100%) |
| APU | 121 | 0 |
| CHU | 205 | 0 |
| SFC | 322 | 0 |
| SOP | 175 | 0 |
| **Total** | **1,557** | **733 (47%)** |

*(Measured 2026-10-14 by loading both CSV sets into a scratch database. Re-derive
with `load_metadata_catalogue` then `load_author_metadata` and count
`DocumentMetadata.objects.filter(author__isnull=False)`.)*

**~53% of the corpus has no author metadata at all** — four of the seven
universities were never surveyed. Those files are reachable only via each author
facet's **"(no value)"** option. 110 of the 235 surveyed people have no catalogue
file at all: survey dates run to 2026-01 while the catalogue covers 2023–2024.

Facet counts, weighted by file: Female 452 (29.0%) · Male 268 (17.2%) · (no
value) 837 (53.8%); Japanese 712 (45.7%) · Malay 21 (1.3%) · Tagalog 8 (0.5%) ·
(no value) 837 (53.8%). `Arabic` and `Korean` are genuinely recorded tongues whose
speakers have no catalogued essays, so the "only offer values with a linked
document" rule correctly hides them.

### Decisions

- **The join key is `(university, name_code)` — never `name_code` alone.** This is
  the correctness case the whole design turns on. `name_code` is **not unique
  across universities**: seven codes appear under two universities in the
  catalogue (`RAKA` → OU+CHU, `NAMI` → KUIS+APU, `BAYU` → SFC+APU, `WAIM` →
  APU+CHU, `SAHI` → KUIS+SOP, `AYU` → KUIS+APU, `RYON` → TUFS+CHU), and five of
  those have survey data under exactly one side. A `name_code`-only match would
  silently give **8 real files another person's demographics**. Enforced by
  `UniqueConstraint(university, name_code)` on `Author`.
- **The filename is *not* parsed, even though it encodes everything.**
  `KUIS2023ACHI101` does decompose as university + year + name code + grade + file
  number — verified against all 1,559 catalogue rows, where every filename matches
  `^[A-Z]+20\d\d[A-Za-z]+\d{3}[a-z]*$` and the embedded values agree with the CSV
  columns in **100% of rows, zero mismatches**. But the columns already carry that
  information, indexed and verified, so a parser would add a failure mode for no
  gain — and it keeps the no-heuristics policy of §6 intact.
- **`Osaka` maps to `OU`, and an unmapped university is an error.** The surveys
  spell the university out; the catalogue uses the filename prefix.
  `UNIVERSITY_ALIASES` reconciles them. An unrecognised value **skips the row and
  exits non-zero**: an author under an unknown university can never link to a file
  and would contribute nothing to any facet — a failure with no symptom.
- **Only the two traits the filter exposes are stored at all.** An earlier revision
  kept every column in a third table, `AuthorSurvey`, one row per occasion, on the
  reasoning that it preserved the data for a future facet. That was wrong twice
  over and the table was removed before shipping. Nothing ever read it — not a
  view, serializer, facet or repository query — so it was purely a second copy of
  the CSV, free to drift with no test able to notice. And the "future facet"
  argument doesn't hold: the CSVs are the authority and are tracked in git, so if
  the client settles a rule, **re-importing is the entire cost** of getting the
  data back. A person is still surveyed up to six times and those rows are still
  grouped in memory during import — that is how the gender conflict is detected
  and the mother-tongue union taken. They are just never persisted per-occasion.
- **Mother tongue is a child table, not a column.** A person can have more than one
  (`TUFS/NEGSEI` has Japanese and Malay). Cells are split on `[、・,，/&;]` after
  NFKC normalisation — the source really does use the ideographic comma, the
  katakana middle dot and the ASCII comma, sometimes in one file.
- **Conflicts resolve to NULL for single-valued traits and to the UNION for mother
  tongue.** There is no non-arbitrary winner between `Female` and `Male`, and a
  filter quietly believing the wrong one is worse than it admitting it doesn't
  know — so gender goes NULL and `Author.has_conflict` is set. Union **invents
  nothing**: every tongue in it was asserted by some survey of that person.
  Resolving `{Japanese, Malay}` vs `{Malay}` to NULL would have cost the Malay
  facet all 21 of its files over one inconsistent row.
- **Every normalised value is stored as a raw/parsed pair** (`*_raw` +
  `*_months`/`*_date`). The client corrects the CSVs at source, so the database has
  to stay reconstructible from what the CSV actually said — including where parsing
  failed.
- **A partial parse is a failure, not a number.** `2、3 year(s)` means "two or three
  years"; matching only the `3 year(s)` and returning 36 months would be a guess
  presented as data. Leftover text containing a digit → `NULL` plus an error;
  leftover with no digit (in practice always `約`, "approximately") → the number is
  kept and the dropped hedge is reported.
- **Durations normalise to months as a `Decimal`** — year=12, month=1, week=1/4.345,
  day=1/30.44 (mean month lengths, so "1 week" and "0.25 month" don't disagree by
  two days). Compound forms (`1 year(s)3 month(s)`) sum, needing no second pattern.
- **The validation report is the deliverable, and the command exits non-zero on an
  error.** The client owns the CSVs and corrects them at source, so an
  inconsistency that is never named never gets fixed. The non-zero exit is so CI
  and deploy scripts notice bad source data rather than marching past it.

### Why only two facets

A person is surveyed twice per academic year (around May and January). **Any value
that changes between those two surveys cannot be attached to a file**, because a
filename records only year and grade — never which survey.

| Column | Invariant per person? | Stable within one academic year? | Ships as a facet |
|---|---|---|---|
| Gender | yes (1 conflict) | yes | **yes** |
| Mother tongue | yes (1 set disagreement) | yes | **yes**, multi-valued |
| Grade | n/a — on the catalogue row, 100% coverage | — | already shipped |
| Age | no | **no** — 87 of 290 person-year buckets hold two ages | no |
| Duration of learning | no | **no** — `KUIS/ACHI` grade 1 reads "1 month" (May 2023) and "9 months" (Jan 2024) | no |
| Difficulty self-rating | no | **no** — 64 of 291 buckets conflict | no |
| Proficiency test level | — | — | no — 91% blank (26 linked files) |
| Place / duration of stay | — | — | no — free text, 36 variants, 3 separators |
| Major | — | — | no — **not comparable across universities** |

Age carries exactly the same ambiguity as duration — the birthday falls between
the two surveys — so it is deferred alongside it rather than shipped on the
assumption that it is stable. `Major` is worth its own note: KUIS and OU record a
**language** (`Indonesian`), TUFS a **faculty** (`School of International and Area
Studies, Oceania`). Filtering on it would behave differently per university; it
needs a controlled vocabulary from the client first.

Promoting one later needs a rule from the client for which survey occasion applies
to a file, a column, an entry in `AUTHOR_CSV_FIELD_MAP`, a facet registration —
and a re-import, which is one idempotent command over CSVs already in git.

### Loading it

Order matters; the author import links against catalogue rows.

```bash
python manage.py migrate
python manage.py load_error_taxonomy        # MUST precede any upload
python manage.py load_metadata_catalogue
python manage.py load_author_metadata --dry-run   # read the report first
python manage.py load_author_metadata
```

Either import order converges — `--relink` re-resolves links for rows that
arrived later, mirroring the catalogue command. Both are idempotent and upsert
rather than replace, because `Author` rows carry resolved document links a
delete-and-recreate would discard. **Mother tongues are the exception: they are
replaced per author on each import**, so a tongue the client removes at source
actually disappears.

Expected on the current data (verified 2026-10-14): **235 authors, 226
mother-tongue rows, 733 linked files** from 437 CSV rows, with **0 errors, 0
warnings, exit code 0**.

> **A stale test depends on the old numbers.**
> `main/tests.py::LoadAuthorMetadataCommandTests::test_loads_the_real_repo_author_csvs`
> wraps the command in `assertRaises(CommandError)` and asserts two
> `has_conflict` authors. Since the CSVs were cleaned the command exits 0 and
> there are zero conflicts, so that test **fails** (the only failure in the
> suite, 228/229 passing). It needs updating to assert a clean import.

### Known source-data issues — for the data owner

**All of the issues below were corrected at source** (commit `6edf94b`, "data
cleaning in source files"). A `load_author_metadata --dry-run` today reports
**0 errors, 0 warnings** and exits 0. The list is kept because it is the
historical evidence for the conflict-handling rules above, and because the same
classes of problem will recur as new survey CSVs arrive.

Reproduce the current state any time with:

```bash
python manage.py load_author_metadata --dry-run
```

**Previously an error — a value could not be used**

| File | Person | Issue | Effect at the time |
|---|---|---|---|
| `KUIS.csv` | `KUIS/KONA` | Gender `Female` in the 2023-05-16 survey, `Male` in 2024-01-10 | `gender` NULL and `has_conflict` set; that person's files answered "(no value)" on the gender facet |

**Previously warnings — imported, with the unusable part left NULL**

| File | Person | Issue |
|---|---|---|
| `TUFS.csv` | `TUFS/NEGSEI` | Mother tongues `Japanese、Malay` in one survey, `Malay` in the other — imported as the union. Worth knowing that **this person is the only Malay speaker with essays in the corpus, so the Malay facet's 21 files still depend on this one row** |
| `TUFS.csv` | `TUFS/CHID` | Duration `2、3 year(s)` — "two or three years"; needed a single value |
| `TUFS.csv` | `TUFS/HOMTAK` | Duration `2カ月` (Japanese notation) |
| `TUFS.csv` | `TUFS/ZOAR` | Duration `８ヵ月` (Japanese notation) |
| `TUFS.csv` | `TUFS/SUGCHIN` | `約2 year(s)` ("about") — read as 24 months, hedge dropped |
| `TUFS.csv` | `TUFS/KIRK` | `約10 year(s)` — read as 120 months, hedge dropped |
| `OU.csv` | `OU/MADA`, `OU/OUJI` | `Date` = `11/27` with no year, so the occasion couldn't be placed in an academic year |

*(The duration and date rows are moot twice over: those columns are no longer
imported at all. A malformed duration in a column nothing queries is not
something to ask the data owner to fix.)*

**Blanks** are a legitimate "not recorded" and import cleanly. **11 authors still
have no gender** and 10 no mother tongue — these are blanks, not conflicts, and
they are why the gender facet's "(no value)" count exceeds the unlinked-file
count. Also around: rows with no date, no age, no grade, no difficulty rating.

> **The importer never deletes an `Author`, and `corpus_db` shows what that
> costs.** The cleaning commit corrected one KUIS name code, `WAKMOM` → `WAMO`.
> On a fresh database that yields 235 authors. `corpus_db` has **236**: the old
> `KUIS/WAKMOM` row survives the upsert and still claims the catalogue file
> `KUIS2023WAKMOM201.txt`, so that file reports demographics sourced from a
> survey row that no longer exists under that code. Mother tongues are *replaced*
> per author, which is why the tongue count is also one high (227 vs 226), and
> linked files likewise (734 vs 733).
>
> Two things to settle with the data owner, not in code: whether
> `KUIS2023WAKMOM201.txt` and `KUIS2023WAMO201.txt` are one student's file under
> a corrected code (then the catalogue's `name_code` needs the same correction and
> the duplicate catalogue row retiring) or two students (then the second needs a
> survey row). Until then, delete the orphan explicitly —
> `Author.objects.filter(university='KUIS', name_code='WAKMOM').delete()`;
> `--relink` will not remove it. Detect the general case by diffing
> `Author.objects.values_list('university','name_code')` against the keys in
> `metadata/authors/*.csv`.

**Coverage gaps.** Before the cleaning, `KUIS/WAMO` had catalogue files but no
survey row — the documented gap is now closed at source (which is what produced
the stale row above). **APU (58 name codes), CHU (85), SFC (119) and SOP
(41) have no survey data at all** — that is what makes ~54% of the corpus "(no
value)" on every author facet. If survey CSVs for these exist, dropping them into
`metadata/authors/` and re-running the command is the whole integration. 110
surveyed people have no catalogue file (expected: survey dates run to 2026-01
while the catalogue covers 2023–2024); they import as authors with no linked
documents and never widen a result.

**Worth the owner's confirmation, not an error.** 19 files whose
filename-encoded grade disagrees with the survey's grade for the same person and
year — e.g. `TUFS2023UEMIS203.txt` is grade 2 by filename and column while that
person's 2023-05-11 survey says grade 3. Nothing depends on this today (grade
comes from the catalogue, not the survey); it would matter only if a per-survey
facet were promoted later. Separately, **124 buckets hold conflicting durations,
93 of them (75%) having ≤1 catalogue file against 2+ survey values** — so a
duration filter would need a client-supplied rule for which survey occasion
applies to a file before it could mean anything.

### Where the author code lives

| Concern | File |
|---|---|
| Models | `main/models.py` — `Author`, `AuthorMotherTongue`, `DocumentMetadata.author` |
| Migration | `main/migrations/0015_author_metadata.py` |
| Parsing, normalisation, linking | `main/author_metadata.py` |
| Command | `main/management/commands/load_author_metadata.py` |
| Django filter | `main/metadata_catalogue.py` — `FACET_FIELDS`, `FACET_ORM_PATHS`, `MULTI_VALUE_FACETS`, `_facet_q`, `_multi_value_facet_q`, `facet_values` |
| Dataplane filter | `dataplane/repositories/shared.py` — `_FACET_COLUMNS`, `_mother_tongue_condition`, `resolve_document_ids` |
| Filter/facet schemas | `dataplane/repositories/base.py`, `dataplane/schemas/metadata.py` |
| Facet values | `dataplane/repositories/postgres/metadata_repository.py` |
| Table mirrors | `dataplane/models/tables.py` (+ 3 registrations in `test_schema_drift.py`) |
| UI | `main/views.py` — `METADATA_FACET_LABELS`, `METADATA_FACET_HINTS`; `components/{metadata_picker,context_bar}.html` |

**Deferred:** age, duration, difficulty, proficiency, place/duration of stay and
major as facets (not imported; each needs a client decision first) ·
per-occasion storage of any kind · a filename parser · an admin CSV upload
endpoint · **refactoring the filter core to a generic facet registry** — five
facets now enumerate themselves in roughly a dozen places across two surfaces;
worth doing, but not in the same change that added the first cross-table facet.
Revisit when a sixth is requested. · KUIS-FE controls for gender/mother tongue
(the API serves them; the Next.js picker doesn't render them yet).

---

## 8. How filtered queries are actually built

### The LEFT OUTER JOIN is the whole trick

Joining the catalogue **from the document side**, `university IS NULL` is true in
*both* required cases — an entry whose field is empty, **and** a document with no
entry at all (the outer join supplied the NULLs). That is the single reason the
"(no value)" option is one expression instead of two branches, why the feature
needed no special-casing per surface, and why **there is no "documents without
metadata" code path anywhere**. Django's ORM reaches the same thing:
`catalogue_entry__university__isnull=True` promotes the join to LEFT OUTER.

### The multi-valued facet uses `EXISTS`, not a join

A join through `AuthorMotherTongue` yields two rows for a two-tongue writer, and
`main/views.py::_analysis_context` sums `token_count` over that same queryset —
`.distinct()` dedupes *rows*, not an aggregate over them, so that writer's tokens
would be counted twice. `EXISTS` asks the membership question without touching
cardinality. `NOT EXISTS` is then the complete "(no value)" term on its own: true
for a document with no catalogue row, one with no author, and an author with no
tongue rows alike — the same three-in-one equivalence the column facets get from
`IS NULL`. (There was precedent for a facet outside the column map: `name_code`
is already special-cased as a prefix `ILIKE`.)

### Semantics and encodings

- **OR within a facet, AND across facets** — including the multi-valued one:
  `(speaks Japanese OR Malay OR has no recorded tongue) AND (gender is Female) AND
  (grade is 2)`.
- **`__none__` is the sentinel, inside each facet's own repeated param** —
  `?university=TUFS&university=__none__` rather than a parallel
  `university_unset=true`. One param per facet, mirroring how the control behaves:
  just another checkbox in the list. Accepted consequence: `year` and `grade` are
  declared `list[str]` and coerced in the service layer, since the sentinel isn't
  an int.
- **Junk on a numeric facet widens rather than 422s.** The filter is a navigation
  control, so a stale URL should never break the page.
- **Integer facets get no `= ''` test** — comparing an integer column to an empty
  string is a type error in Postgres.
- **Facet values come only from entries with a linked document.** Offering
  "University: OU" when no OU file is uploaded hands the user a guaranteed-empty
  filter. Global, not scoped to the current corpus selection — a scoped list would
  reshuffle the checkboxes on every tick. (Matches `/api/v1/taxonomy`'s behaviour.)
- **The `__none__`/`UNSET` sentinel is duplicated across the Django/dataplane
  boundary** rather than shared — the data plane can't import Django code, and
  `api_corpus.py` set the precedent for duplicating over reaching across surfaces.
- **KUIS-FE carries three encodings** of the same selection (`lib/metadataFilter.ts`):
  Sets in state, comma-joined in the URL, repeated for the API — and **nine**
  query-string builders (three hooks plus six hand-built URL-mirror/export strings
  in `RankedSearch.tsx`, `ErrorAnalytics.tsx`, `ngrams-search.tsx`,
  `kwic-search.tsx`, `kwic-fast-search.tsx`), a pre-existing duplication any new
  filter param has to keep in step with.

### The API surface

| Method | Path | Permission |
|---|---|---|
| GET | `/api/v1/metadata-facets` | authenticated |
| GET | `/api/v1/{frequency,collocations,ngrams,kwic,error-frequency,epic,error-summary}` | authenticated |
| GET | `/api/documents/` (`?has_metadata=false` finds exactly the files the catalogue misses) | authenticated |

All seven analysis endpoints take `university`, `year`, `grade`, `gender`,
`mother_tongue` (repeated, OR within a facet, AND across facets) and `name_code`
(prefix, case-insensitive, LIKE wildcards escaped). **No staff gate on
filtering** — error analytics set that precedent; the importer is admin-only by
being a management command. `/api/documents/` nests each document's catalogue
entry read-only, since writes go through the command and the CSV is the authority.

The filter params are the one shared **request** schema in `dataplane/schemas/` —
a `Depends()`-able `MetadataFilterParams`, because declaring them inline would
mean 28 duplicated `Query()` declarations.

**Adding a third author facet** is: one entry in `FACET_FIELDS` +
`FACET_ORM_PATHS`, one in `_FACET_COLUMNS`, one field on
`MetadataFilter`/`MetadataFilterParams`/`MetadataFacets`, one label, and one line
in the repository's facet query. The filter *application* sites do not change.

---

## 9. Performance characteristics, limits, and planned work

### Measured, not guessed

The numbers below came from the live database and from benchmarking the real
parser (`main/corpus_parsing.py`) against a real sample file
(`info/KUIS2022NAMI3.xml`, 7670 bytes → 490 tokens): **~1.7M tokens/sec**.

**Storage is not the scaling risk.** Projected at ~170–190 bytes/token all-in,
doubled for the two modes: 1M words ≈ 0.4 GB, 10M ≈ 3.5 GB, 100M ≈ 35 GB.
Postgres handles this without complaint. (Measured on the pre-normalisation
schema, the token table showed a **2.7:1 index-to-data ratio**, with the composite
index alone larger than the data it indexed — part of why Tier 1 was worth doing.)

**The read path is the actual problem, on the Django surface.** Re-parse cost
**per request**: 1M tokens ≈ 0.6 s, 10M ≈ 5.8 s, 50M ≈ 29.2 s. Per *request*, not
per session — there is still **no caching layer at all**. And `_rank_counter()`
builds the complete row list in Python *then* hands it to `Paginator`, so page 2
costs exactly what page 1 cost; `kuis.js` auto-submits the filter form, amplifying
it.

**N-gram memory is the hard wall.** Building a Python `Counter` over tuples,
benchmarked on a Zipfian synthetic corpus (300k tokens, 25k types):

| n | bytes/token | @ 10M tokens | @ 50M tokens |
|---|---|---|---|
| 1 | 4.6 | 0.04 GB | 0.2 GB |
| 2 | 105.7 | **0.98 GB** | 4.9 GB |
| 3 | 127.7 | **1.19 GB** | 5.9 GB |
| 5 | 162.9 | **1.52 GB** | 7.6 GB |

**Per request, per worker.** Four concurrent users requesting 5-grams over a
10M-token corpus ≈ 6 GB. This OOMs; it does not gracefully degrade. Concurrency is
worse than linear — each WSGI worker holds its own copy of the corpus text, the
derived word lists and the `Counter`, with zero amortisation anywhere. Memory
exhaustion arrives before CPU saturation.

**All of this applies to the Django surface only.** The FastAPI path sums
precomputed aggregates and is unaffected by corpus size in the same way.

### What the staged optimisation plan has and hasn't delivered

The original plan ran in five tiers. Four are done:

| Tier | What it was | Status |
|---|---|---|
| **0** — one source of truth | Move the live-parse features onto indexed data instead of re-parsing | ✅ on the data plane (every feature reads tables). The Django views still re-parse and are scheduled to retire |
| **1** — normalise the vocabulary | `WordType` + `Token.word_type` FK replacing the `varchar` | ✅ shipped (§2) |
| **2** — precompute aggregates | `DocumentWordFreq`, `DocumentNgram` | ✅ shipped (§2) |
| **3** — indexing out of the request | `transaction.atomic`, `bulk_create(batch_size=5000)`, a task queue, a `status` field, no `post_save` signal | ✅ shipped (§3) |
| **4** — caching | Redis-backed cache at the service layer | ⬜ **not started** |

Stretch items from Tier 1 that were **not** done: `mode` as `smallint`/`bool`
instead of a `varchar(10)`; storing a corrected token only when it differs from
the original (for a mostly-plain-text corpus this would halve the table — today
`_flat_tokenize_both()` returns `words, list(words)`, so **every token of a plain
text document is stored twice**); a `BRIN` index on `document_id` instead of btree
(rows are inserted in document order, so BRIN would be nearly free); a composite
PK `(document_id, mode, position)` replacing the `bigint` surrogate, which would
remove an entire index and give clustering for free.

### Caching (Tier 4) — the cheapest large win left

Keyed on the feature plus its full scope, caching the computed result turns the
second pageview of a result set from ~6 seconds into milliseconds, and it is
orthogonal to every other tier. Redis already exists as the Celery broker.

**The key is wider than the original plan assumed.** It is not
`(corpus ids, mode, feature, n)`: the metadata filter is a second scoping axis
and the error-analytics code filter a third, so **any cache key has to include
all five metadata facets plus the `__none__` sentinel per facet, and the error
code set** — or a filtered request will serve an unfiltered result. The authority
for what the filter carries is `MetadataFilter` in
`dataplane/repositories/base.py`, not a list duplicated in a doc.

### Open defects and gaps

- **No staleness check exists.** `tokenized_hash` and `tokenizer_version` were
  added precisely so `retokenize` could become
  `WHERE tokenized_hash IS DISTINCT FROM content_hash OR tokenizer_version < CURRENT`
  — detecting content edited without reindexing, tokens lost to a half-failed
  upload, and tokens built by a superseded tokenizer. **That query is not
  implemented.** `worker/tasks/maintenance.py` doesn't exist; `retokenize` is a
  management command you run by hand, over everything.
- **`retokenize` has no `.iterator()`** — it loads every document's full content
  into RAM at once.
- **No `defer('content')` on analysis querysets** (`_get_selected_documents`), so
  full document text is loaded even where only counts are needed; `_analysis_context`
  also re-queries `aggregate()`/`count()` on an already-evaluated queryset.
- **A project-wide `retokenize` has never been run for real** on `corpus_db`. A
  dry-run showed nothing currently needs it, which is not the same as having done
  it.
- **The Django live-parse views are superseded but still live**, by design — the
  strangler-fig migration never deletes a path until its replacement is confirmed
  to cover it. Retiring them is the other half of the caching phase. Legacy KWIC's
  phrase search is already proven to be a strict subset of the data plane's, so it
  is the first candidate; it is worth a product confirm before removing it.
- **`corpus_db` carries one stale `Author` row** (`KUIS/WAKMOM`) that a fresh
  import would not create — §7. The importer upserts and never deletes, so any
  key corrected at source leaves its old row behind, still claiming whatever it
  linked to.

---

## 10. Testing the data paths

```bash
# Django — 229 tests, SQLite, no Postgres needed
CELERY_TASK_ALWAYS_EAGER=1 DB_ENGINE=django.db.backends.sqlite3 DB_NAME=":memory:" \
  python manage.py test main

# Data plane — 171 tests, needs a REAL Postgres whose DB_NAME contains "test"
CELERY_TASK_ALWAYS_EAGER=1 DB_NAME=kuis_dataplane_test python manage.py migrate --noinput
CELERY_TASK_ALWAYS_EAGER=1 DB_NAME=kuis_dataplane_test python -m pytest dataplane/tests -v
```

Two constraints worth internalising:

- **The data plane suite needs a real Postgres** — asyncpg has no SQLite
  equivalent — **and its `DB_NAME` must contain "test"**: the fixture refuses
  otherwise, because it truncates `Document`/`Corpus` rows.
- **`CELERY_TASK_ALWAYS_EAGER=1` is required by both**, since the `post_save`
  signal is gone and fixtures call the real `index_document.delay(...)`.

**What the parity testing established**, and why it is worth keeping up: each
FastAPI port was compared against the equivalent Django view's CSV export for a
fixed corpus selection, checked for exact row **set** *and* exact row **order**
(including the count-tie alphabetical case) — exact match every time, on a real
Postgres database seeded with known data. KWIC was additionally parity-checked
byte-for-byte against both `/analysis/kwic/search/export/` (single word) and
`/analysis/kwic/export/` (phrase), including document-boundary window truncation.
That is the evidence base for believing the two surfaces agree; a new repository
should earn the same.

Bugs that only a real database or a real browser found, as a reminder of what
unit tests don't cover here: a self-join `ORDER BY` referencing the unaliased
table (rejected only at execution time, invisible from reading the SQLAlchemy
code); `Sum()` over a corpus with zero documents serializing as JSON `null`
rather than `0`; and CORS, which curl neither sends nor enforces — every test
before the first real browser run would have looked identical whether CORS was
configured or not.

**Not verified anywhere:** browser click-through of the taxonomy tri-state
checkbox visuals, the summary tree's disclosure toggles, and the metadata
picker's rendering inside KUIS-FE's `ContextBar` — all were checked by
server-rendered output and tests, not by clicking.
