# KUIS — Storage & Performance Review / Optimisation Plan

> Architecture review of how file data (tokens) is currently stored and queried, with a staged optimisation plan. Based on a read of the codebase as of 2026-09-22 (branch `xml-support-plus-ui`), plus measurements taken against the live `corpus_db` database and benchmarks of the actual parser. **No code was changed to produce this document.**

---

## 1. How Token Data Is Stored Today

There are **two parallel representations of the same text**, and they are not used by the same features.

### A. `Document.content`

The raw XML/plain text, held in a Postgres `TextField` (`main_document.content`). Above ~2 KB Postgres TOASTs it — stored out-of-line and compressed.

### B. `main_token` — one row per token, *per mode*

`main/models.py` → `Token`:

| Column | Type |
|---|---|
| `id` | `bigint` (surrogate PK) |
| `document_id` | `bigint` FK → `main_document` |
| `word` | `varchar(100)` |
| `position` | `integer` |
| `mode` | `varchar(10)` — `'original'` / `'corrected'` |

Indexes: `main_token_pkey`, `(word)`, `(document_id)` (FK), `(document, mode, position)`.

**Every token is stored twice** — once as `mode='original'`, once as `mode='corrected'` — even when the document is plain text and the two streams are byte-identical, because `_flat_tokenize_both()` in `main/corpus_parsing.py` returns `words, list(words)`.

### Write path

`post_save` signal on `Document` → `build_tokens()` → tokenize both streams → a single **unbounded** `bulk_create()` → a second `save(update_fields=[...])` to write back `token_count` / `token_count_corrected`. All synchronous, inside the HTTP request, with **no transaction wrapper**.

### Read path — which feature reads which store

This is the crux of the review.

| Feature | Reads from |
|---|---|
| Word frequency | **re-parses `Document.content` live** |
| Collocations | **re-parses `Document.content` live** |
| N-grams | **re-parses `Document.content` live** |
| KWIC (legacy, `kwic`) | **re-parses `Document.content` live** |
| KWIC (fast, `kwic_search`) | `main_token` |
| Token-count badges | denormalised ints on `Document` |

The `Token` table is the larger half of total storage and serves **exactly one of five** analysis features. Everything else pays its write cost and storage cost and gets nothing back.

---

## 2. Does It Scale?

### 2.1 Storage — not the problem

Measured against the live database (3 documents, 1090 token rows):

| Object | Size |
|---|---|
| `main_token` total | 864 kB |
| &nbsp;&nbsp;heap only | 232 kB |
| &nbsp;&nbsp;`(document, mode, position)` index | 288 kB |
| &nbsp;&nbsp;`main_token_pkey` | 128 kB |
| &nbsp;&nbsp;`(word)` index | 56 kB |
| &nbsp;&nbsp;`(document_id)` FK index | 56 kB |

Indexes total 632 kB against 232 kB of heap — a **2.7 : 1 index-to-data ratio**, with the composite index alone larger than the data it indexes. Sampled rows are ~57–65 bytes on disk.

Projected at ~170–190 bytes/token all-in, doubled for the two modes:

| Corpus size (words) | Token rows | Estimated DB size |
|---|---|---|
| 1M | 2M | ~0.4 GB |
| 10M | 20M | ~3.5 GB |
| 100M | 200M | ~35 GB |

Postgres handles this without complaint. **Storage volume is not the scaling risk.**

### 2.2 Read path — the actual problem

Benchmarked against the real parser (`main/corpus_parsing.py`) and a real sample file (`info/KUIS2022NAMI3.xml`, 7670 bytes → 490 tokens): **~1.7M tokens/sec**.

| Corpus size | Re-parse cost **per request** |
|---|---|
| 1M tokens | 0.6 s |
| 10M tokens | 5.8 s |
| 50M tokens | 29.2 s |

Per *request*, not per session — `grep CACHES` over `config/` returns nothing. **There is no caching layer at all.**

`_rank_counter()` builds the complete row list in Python and *then* hands it to `Paginator`, so page 2 costs exactly what page 1 cost. Every sort click, every filter change, every pagination step re-parses the entire selected corpus. `main/static/main/kuis.js` auto-submits the filter form, amplifying this.

### 2.3 Memory — the hard wall

N-grams build a Python `Counter` over tuples. Benchmarked on a Zipfian synthetic corpus (300k tokens, 25k types — realistic type/token behaviour):

| n | bytes/token | @ 10M tokens | @ 50M tokens |
|---|---|---|---|
| 1 | 4.6 | 0.04 GB | 0.2 GB |
| 2 | 105.7 | **0.98 GB** | 4.9 GB |
| 3 | 127.7 | **1.19 GB** | 5.9 GB |
| 5 | 162.9 | **1.52 GB** | 7.6 GB |

This is **per request, per worker**. Four concurrent users requesting 5-grams over a 10M-token corpus ≈ 6 GB. This OOMs — it does not gracefully degrade.

### 2.4 Behaviour under concurrency

Worse than linear. Each WSGI worker holds its own full copy of the corpus text, the derived word lists, and the `Counter`. N concurrent analysis requests = N full corpus parses with **zero amortisation** anywhere. Memory exhaustion arrives before CPU saturation.

---

## 3. Specific Defects

| # | Defect | Location | Impact |
|---|---|---|---|
| 1 | **Unbounded `bulk_create`** — Postgres uses Django's base `bulk_batch_size` → `len(objs)`, i.e. no batching (confirmed in `venv/.../db/backends/base/operations.py:80`; Postgres does not override it) | `models.py` `build_tokens()` | A 1M-token document builds 2M Python objects and one giant INSERT |
| 2 | **No transactions** — `grep atomic` over `main/` is empty | upload paths | Document INSERT / token INSERT / count UPDATE are three autocommits; a mid-failure leaves wrong counts or missing tokens, permanently and silently |
| 3 | **`_word_index_matches` materialises every hit** — `list(...)` of all `(doc_id, position)` before paginating | `views.py:935` | Searching a high-frequency Indonesian function word (`yang`, `di`) pulls a large fraction of all token positions into Python to render 50 rows |
| 4 | **`_hydrate_word_index` loads whole documents** to slice a ±N window | `views.py:951` | Should be `position__range=(p-w, p+w)` per hit |
| 5 | **No `defer('content')`** on analysis querysets | `views.py` `_get_selected_documents()` | Full document text loaded even where only counts are needed; `_analysis_context()` also re-queries `aggregate()`/`count()` on an already-evaluated queryset |
| 6 | **Signal-based indexing** | `models.py` `post_save` receiver | Write path invisible at the call site, unbatchable, undeferrable, untestable; forces `retokenize` to work around it |
| 7 | **`retokenize` has no `.iterator()`** | `management/commands/retokenize.py` | Loads every document's full content into RAM at once |
| 8 | **Identical streams stored twice** | `corpus_parsing.py` `_flat_tokenize_both()` | Waste proportional to the plain-text share of the corpus |

---

## 4. Recommendations

### Tier 0 — Pick one source of truth

Two representations are currently maintained and neither is trusted. Move the four live-parse features onto the `Token` table:

- Word frequency becomes `GROUP BY word` — Postgres answers this by index-only scan regardless of corpus size.
- Pagination becomes `LIMIT/OFFSET` (or keyset) instead of "build 25M rows in Python, then slice".

*Alternative:* drop the `Token` table entirely, keep only `content`, and cache parsed word lists. Cheaper to build, but caps you at corpora that fit in memory and keeps every query O(corpus). Defensible as a ~6-month stopgap **only** while the corpus stays under ~1M tokens.

### Tier 1 — Normalise the vocabulary

Highest-leverage single schema change:

```
WordType: id serial, form varchar(100) UNIQUE     -- the distinct surface forms
Token:    document_id, word_type_id int, position int, mode smallint
```

Heaps' law means types grow roughly as √tokens — a 10M-token Indonesian corpus has perhaps 200–300k types. Storing an int FK instead of a `varchar`:

- Heap row drops from ~64 to ~24 bytes (with `mode` as `smallint`).
- The `(word)` index becomes an index on a small table.
- The large composite index shrinks proportionally.
- `GROUP BY word_type_id` becomes an integer operation.
- Realistically **~3x smaller and materially faster aggregates.**

Alongside it:

- `mode` → `smallint` or `bool is_corrected` — saves ~9 bytes/row in the heap *and* inside the largest index.
- Store a corrected token only when it differs from the original (or keep the corrected stream as a sparse diff). For a mostly-plain-text corpus this halves the table.
- Consider `BRIN` on `(document_id)` instead of btree — rows are inserted in document order, so BRIN is nearly free.
- Consider a composite PK `(document_id, mode, position)` and drop the `bigint` surrogate, removing an entire index and giving clustering for free.

### Tier 2 — Precompute aggregates

Don't compute frequency from tokens at query time either. Materialise:

```
DocumentWordFreq: document_id, word_type_id, mode, count
DocumentNgram:    document_id, ngram_id, n, mode, count    -- top-K only
```

A corpus query becomes `SUM(count) GROUP BY word_type_id WHERE document_id IN (...)` — a few thousand rows per document instead of hundreds of thousands. This is the query shape every serious corpus tool uses (Sketch Engine, CQPweb, IMS Corpus Workbench), and it is what makes multi-corpus selection **additive** rather than O(corpus).

N-grams should be top-K per document with a frequency floor. Storing all 5-grams is storing the corpus again at 5x multiplicity, and the tail is never read.

### Tier 3 — Move indexing out of the request

- Wrap upload in `transaction.atomic`.
- `bulk_create(..., batch_size=5000)`.
- Push tokenisation to a task queue for anything beyond small files; mark the Document `indexing` / `ready`. Even without Celery, a management command plus a status field beats a 30-second upload request.
- Replace the `post_save` signal with an explicit `index_document()` call.

### Tier 4 — Add caching

There is currently none. Keyed on `(sorted corpus ids, mode, feature, n)`, caching the computed `Counter` turns the second pageview of a result set from ~6 seconds into milliseconds. **Cheapest large win available today**, and orthogonal to every other tier.

---

## 5. On the Planned Content-Hashing Work

The instinct is right and it composes well with the above — but be precise about what it buys, because **dedup is probably not the main prize**.

### What it genuinely gives you

**1. Idempotent ingest.** Re-uploading the same file becomes a no-op instead of a silent duplicate that skews every frequency count. In a learner corpus where the same essay arrives through two channels, this is a correctness fix, not a storage saving.

**2. The actual fail-safe.** Don't hash only the content — hash the *tokenisation inputs*:

```
Document.content_hash      = sha256(canonicalized content bytes)
Document.tokenized_hash    = the content_hash the current Token rows were built from
Document.tokenizer_version = int, bumped when corpus_parsing.py changes
```

`retokenize` then stops being an O(whole corpus) rebuild and becomes:

```sql
WHERE tokenized_hash IS DISTINCT FROM content_hash
   OR tokenizer_version < CURRENT_VERSION
```

That detects (a) content edited without reindexing, (b) tokens lost to a half-failed upload — i.e. defect #2 above — and (c) tokens built by a superseded tokenizer. **None of these are currently detectable**; today the only remedy is to re-run everything and hope. This is also what makes the two-source-of-truth situation *auditable*.

**3. Reproducibility.** Hash the Corpus as a hash of its sorted member hashes and you get a citable corpus version ID. If a paper cites "corpus X, 1.2M tokens", the hash set pins exactly which files that was. This matters more in linguistics than in most domains.

### Pitfalls to design around

- **Canonicalise before hashing**, or you will dedup nothing. BOM, line endings, trailing whitespace and XML attribute order all vary between exports of the same document. For XML, hash the canonicalised serialisation (`ET.canonicalize`), not the raw string — but keep the raw bytes.
- **Exact hashing misses near-duplicates**, which is the likelier contamination in a learner corpus: the same essay with one typo fixed, or a shared prompt paragraph copied across submissions. If duplicate-driven skew is the real worry, you need shingling / MinHash *on top*, not instead.
- **Ownership becomes ambiguous.** `Document.user` is currently the uploader; once content is deduplicated, one blob has many uploaders. Split the model:
  - `TextBlob` — content-addressed: `hash`, `content`, and the Token rows.
  - `Document` — `title`, uploader, `uploaded_at`, `blob_id`, corpus memberships.

  Token rows then hang off the blob, so two uploads of the same text share one token index. **This is where the real storage win lands.** Without the split, "who uploaded this" and "may this be deleted" get answers you don't want.
- **Scope the `UNIQUE` constraint deliberately.** The same text under two titles may legitimately be two Documents (two students, same prompt) — in which case dedup the blob and its tokens but keep both Documents.
- Index as `varchar(64)` / `bytea` with a btree unique. A hash index buys nothing here and cannot enforce uniqueness cleanly.

---

## 6. Bottom Line

Volume is not the scaling risk. In order of what breaks first:

1. **N-gram memory exhaustion** — ~1–1.5 GB per request per 10M tokens. A hard wall, not a gradient.
2. **Full corpus re-parse per pageview with no cache** — makes every interaction O(corpus), and concurrency multiplies it.
3. **The `Token` table is 2x-redundant and serves one feature of five** — full write and storage cost, fractional benefit.

**Suggested sequence, cheapest to most valuable:**

1. Add caching — days.
2. Move analysis features onto SQL aggregates over `Token` — weeks.
3. Normalise word types and precompute per-document frequencies — the real fix.
4. Content hashing as the integrity layer throughout.

One framing note on the hashing work: do it, but justify it internally as **integrity and reproducibility**, not as a space saving. In a corpus of unique student texts the dedup ratio will be near 1.0 — it is the `tokenized_hash` staleness check that earns its keep.
