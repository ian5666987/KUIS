# Hosting and capacity planning

How much server KUIS (this repo) and KUIS-FE need, and when each tier runs
out. Numbers marked **estimated** are derived and flagged as such — don't treat
them as the same kind of fact as a measurement.

**Where the measurements come from (re-measured 2026-10-19/20):**

- **Today** is the real annotated corpus: the 298 XML files in
  `Koper/Results/Eror-XML` (APU, CHU, KUIS, SFC, SOP and TUFS 45 each, OU 28).
  They were loaded through the real `get_or_create_document` →
  `index_document` → `aggregate_document` path into a throwaway database.
- **Catalogue size** (1,559 documents, every essay in `metadata/*.csv`) and
  **T1 size** (9,354 documents) are synthetic. Each document is generated at a
  catalogue row's real word count, with a Zipfian vocabulary, and indexed
  through the same path. See `docs/caching-plan.md` §2.
- All runs used Postgres 18.1 on an Apple M2. Storage figures carry over to
  production's `postgres:16` essentially unchanged. Timings are relative, not
  production latencies.
- Still from an earlier two-mode run, before migration `0017`, and labelled
  where they appear: the cold vs. warm KWIC comparison (§4) and the KWIC rows of
  §5.

---

## 1. What is actually being hosted

Two independent stacks, each in its own repo, both behind the separately
deployed `nginx-gateway` on the shared `app-shared` Docker network. No app
service publishes a host port (`docker-compose.yml`).

| Service | Image / runtime | Role |
| --- | --- | --- |
| `kuis-postgres` | `postgres:16` | Single source of truth. Django owns the migrations; the data plane reads the same tables. |
| `kuis-django` | `python:3.14-slim` + gunicorn | Auth/RBAC, users, corpus + file metadata, admin, uploads. |
| `kuis-fastapi` | `python:3.14-slim` + uvicorn ×4 | Read-only analytics: KWIC, frequency, n-grams, collocations, error analytics. |
| `kuis-worker` | `python:3.14-slim` + Celery | Tokenization and all Tier-2 aggregate computation. |
| `kuis-beat` | same image as `kuis-worker` | Celery beat: enqueues the hourly stale-token sweep. Negligible load; exactly one instance. |
| `kuis-redis` | `redis:7-alpine` | Celery broker and result backend (`worker/celery_app.py`). No `maxmemory` set. |
| `kuis-cache` | `redis:7-alpine` | **Proposed (Phase 8), deferred, not deployed.** Capped, evicting result cache for the data plane. See `docs/caching-plan.md`. |
| KUIS-FE | Next.js 16 / React 19, `next start` | SSR frontend. No Dockerfile in the repo yet. |

---

## 2. Assumptions

Stated up front because every tier below is just these numbers multiplied.

### Corpus shape — measured on the real annotated corpus (n = 298)

| Property | Value |
| --- | --- |
| Documents today | **298** annotated, of 1,559 essays in the catalogue (`metadata/*.csv`) |
| Total words today | **37,758** |
| Mean words/document | **127** |
| Median words/document | 67 |
| p90 / max | 408 / 678 (min 15; 3 documents exceed 500 words) |
| Stored XML size | ~19.4 bytes per word |
| Error annotations | **0.126 per word** (4,758 segments) |

The annotated set is more skewed than the catalogue as a whole. The catalogue
has a similar mean (124 words) but its median is 94 and its p90 301. That is why
the catalogue-size and T1 runs use the catalogue's distribution rather than
today's.

**These are short student essays, not books.** The entire corpus today is
about **237 KB of plain text**. Sizing this system in "GB of corpus" is
misleading: the raw text is negligible and the *derived* tables are
everything. All tiers below are therefore stated in documents and words,
with a raw-text figure given only for translation.

### Derived-row multipliers — measured on the real corpus

The worker writes, per source word:

| Table | Rows per source word | Why |
| --- | --- | --- |
| `main_token` | **1.00** | one row per word, as written (the corrected stream was removed in `0017`) |
| `main_documentwordfreq` | **0.53** | complete per-document type counts. Real learners repeat words within an essay; the Zipfian synthetic text gives 0.73 |
| ~~`main_documentngram`~~ | ~~3.73~~ | **dropped in `0019` (7.13)**; n-grams are counted from `main_token` at query time (§3) |
| `main_errorannotation` | 0.126 | one per `<segment>` |

### Workload

- Reads dominate. Writes happen in bursts at bulk ingest, then go quiet.
- An active user fires an analysis query roughly every 20–30 s while working.
- "Concurrent" below means genuinely in-flight requests, not logged-in sessions.
  A ~5–10% concurrent-to-named ratio is assumed.
- Single region, single AZ. No HA requirement below Tier 3.
- Backups: nightly `pg_dump` kept locally plus shipped off-box.

---

## 3. Measured storage, at today's corpus

The real corpus, all derived tables populated, after `VACUUM ANALYZE`. This was
measured just before migration `0019` dropped `main_documentngram`. After it, the
same corpus measures **20.7 MB** in total.

| Table | Rows | Heap | Indexes | Total | Bytes/row |
| --- | --- | --- | --- | --- | --- |
| `main_documentngram` | 140,893 | 13.6 MB | 5.2 MB | **18.8 MB** | 140 |
| `main_token` | 37,758 | 2.2 MB | 2.7 MB | **4.9 MB** | 137 |
| `main_documentwordfreq` | 20,100 | 1.2 MB | 1.7 MB | **2.9 MB** | 150 |
| `main_errorannotation` | 4,758 | 0.7 MB | 0.6 MB | 1.3 MB | 291 |
| `main_document` | 298 | 0.6 MB | 0.1 MB | 0.9 MB | 3,271 |
| `main_wordtype` | 4,813 | 0.2 MB | 0.5 MB | 0.7 MB | 160 |
| `main_documenterrorfreq` | 2,344 | 0.1 MB | 0.3 MB | 0.5 MB | 199 |
| `main_documentmetadata` | 1,557 | 0.3 MB | 0.5 MB | 0.9 MB | 589 |
| **Corpus data** (every table above except the catalogue) | | | | **30.2 MB** | |
| **Whole database** | | | | **40.7 MB** | |

The other ~10.5 MB is fixed: system catalogues, auth, the metadata catalogue
and the taxonomy. It doesn't grow with the corpus.

The synthetic runs give **167 MB** at catalogue size (1,559 documents,
0.19 M words) and **926 MB** at T1 size (9,354 documents, 1.16 M words).

Two constants fall out of this, and they drive every tier:

- **~0.3 KB of Postgres per source word**, plus ~10 MB fixed. With the n-gram
  table it was ~0.82 KB, a figure the real corpus and the synthetic runs agreed
  on to two significant figures.
- **~35 KB of Postgres per document** (at the 127-word mean).

That is a ~**45×** blow-up over the raw text. It was ~130× with the n-gram
table, which alone was **62%** of the corpus data. Before `0017` the
catalogue-size synthetic corpus measured 350 MB, about 1.9 KB per word.

### The per-document n-gram table — dropped (7.13)

Decided on 2026-10-22 and shipped as migration `0019_drop_document_ngram`. The
analysis that decided it is kept below, because it is the evidence behind the
change.

**Why it is so large.** With `FREQUENCY_FLOOR = 1` and a 127-word essay, almost
every n-gram occurs once in its document, so the table stores ~3.7 rows per
source word. In effect it is a second copy of the token stream, cut into
windows. Measured on the real corpus: **96.9% of its rows have `count = 1`**.

**Option 1: raise the floor to 2. Rejected; it breaks the feature.** A floor
of 2 would shrink the database from 40.7 MB to ~22.5 MB. But repetition in
this corpus happens **across** documents, not within them, and a per-document
floor drops exactly the rows that add up to the corpus-level counts the feature
displays.

Measured on the real corpus, against true corpus counts computed from
`main_token`:

| n | Rows today | Rows with floor 2 | Share of occurrences still counted | Top-100 n-grams with the correct count | Repeated n-grams (corpus count ≥ 2) still present |
| --- | --- | --- | --- | --- | --- |
| 2 | 32,839 | 2,974 | 20.1% | 1 / 100 | 41.8% |
| 3 | 35,682 | 981 | 6.0% | 1 / 100 | 31.2% |
| 4 | 36,210 | 333 | 1.9% | 0 / 100 | 21.8% |
| 5 | 36,162 | 119 | 0.7% | 0 / 100 | 12.9% |

A floor of 2 isn't a smaller version of the same feature. It is a different
feature that returns wrong counts.

**Option 2: drop the table and compute n-grams from `main_token`.
Chosen.** A single-scan query does it:
`lead(form, i) OVER (PARTITION BY document_id ORDER BY position)`, then
`GROUP BY`, `ORDER BY count`, `LIMIT` in SQL. Only the page's 50 rows come
back, and `type_count`/`result_count` come from window counts in the same
statement.

Timed end to end through asyncpg against the read path it replaced
(`compute_ngram_agg_page`, since removed), whole-corpus scope, page 1 by count, median of 5
warm runs:

| Size | n | `DocumentNgram` (today's path) | `lead()` over `main_token` | Speed-up |
| --- | --- | --- | --- | --- |
| Today (real, 0.04 M words) | 2 / 3 / 5 | 144 / 134 / 176 ms | **39 / 48 / 58 ms** | 2.8–3.7× |
| Catalogue (synthetic, 0.19 M) | 2 / 3 / 5 | 536 / 697 / 762 ms | **217 / 285 / 378 ms** | 2.0–2.5× |
| T1 (synthetic, 1.16 M) | 2 / 3 / 5 | 2,340 / 3,596 / 4,197 ms | **1,284 / 1,557 / 1,856 ms** | 1.8–2.3× |

The same holds with a `q` filter: today 33–101 ms against 86–168 ms. The old
n-way self-join fallback (`_ranked_query.py`) is slower than both: 150 / 262 /
553 ms today.

Why dropping the table is justified:

- **It is faster without the table**, at every size measured. The current path
  sends every candidate n-gram to Python (35k rows today, 1.1 M at T1) to
  resolve ids, filter and sort. `lead()` does all of that in Postgres.
- **It is exact; the table isn't.** `TOP_K_PER_DOCUMENT = 500` truncates any
  document with more than 500 distinct n-grams of one size. On the real corpus
  that is 3 essays, and they lose 0.14% (bigrams) to 0.77% (5-grams) of all
  occurrences: 21,644 distinct bigrams reported against a true 21,684. On the
  larger synthetic corpora the undercount reaches the top of the list ("nigi
  nuyu": 5,013 against 5,015).
- **It frees 62% of corpus storage.** Corpus data drops from ~0.82 to
  ~0.31 KB per word: 30.2 → 11.4 MB today, ~40 → ~15 GB at T3 (**estimated**).
  It also removes ~3.7 of the ~5.4 rows written per word at indexing time.
- **It works on any document selection.** A metadata filter resolves to an
  arbitrary set of documents. `lead()` handles that as-is; a corpus-level
  rollup (§8) can only serve whole named corpora.

What it doesn't do: it is still linear in the words selected. It moves the
ceiling in §8 by ~2× rather than removing it.

**As shipped** (`dataplane/repositories/postgres/_window_ngram_query.py`), on the
real corpus, end to end through the Service:

- an n-gram page takes **44–54 ms** (was 132–154 ms);
- a collocations page takes **37 ms** (was 94 ms);
- every one of 20 pages checked across n, sort, match mode and offset matches
  the Tier-0 self-join exactly.

At T1 the window sort over 1.16 M tokens is close to the default
`work_mem = 4MB`. Check `EXPLAIN (ANALYZE, BUFFERS)` for an external sort
before going past catalogue size, and raise `work_mem` for the data plane's
role if one appears.

---

## 4. Measured query cost

Per request, through the real Service classes (not raw SQL), warm, with the
whole corpus selected, which is the worst-case scope. The full table, including
the cheaper features and the cost of a cache hit, is in
`docs/caching-plan.md` §3.

| Request | Today (real, 0.04 M words) | Catalogue (synthetic, 0.19 M) | T1 (synthetic, 1.16 M) |
| --- | --- | --- | --- |
| N-grams page, n = 5 | 53 ms | 378 ms † | **1.86 s** † |
| Collocations page | 37 ms | 217 ms † | **1.28 s** † |
| KWIC, most common word, `sort=left` | 111 ms | **1.78 s** | **1.94 s** |
| Word frequency page | 25 ms | 124 ms | 607 ms |
| Error frequency page | 13 ms | 35 ms | 148 ms |
| KWIC, default sort | 6–14 ms | 19–33 ms | 79–114 ms |
| EPIC / error summary | 5–11 ms | 9–21 ms | 47–88 ms |

† The `lead()` query itself, measured before it shipped (§3), without the
Service's document-id lookup (2–13 ms). Before 7.13 these two rows were
756 ms / 4.25 s and 526 ms / 2.29 s.

From the earlier two-mode run, raw SQL:

| Query | Cold | Warm |
| --- | --- | --- |
| 2-word KWIC (self-join on `main_token`) | **338 ms** | **50 ms** |
| 3-word KWIC, uncommon first term | — | 0.8 ms |
| One document's full write path (248 + 155 + 968 rows) | — | **10 ms** |

Four conclusions:

1. **Today, nothing is slow.** The worst request on the real corpus takes
   111 ms (KWIC `sort=left`).
2. **Cold vs. warm is a 6.7× difference on KWIC.** The cold run read 2,410
   heap pages (~19 MB of random I/O). Sizing RAM so Postgres keeps the token
   indexes resident is the single highest-value decision here — and the disk
   must be NVMe, never network HDD.
3. **N-gram and collocation pages are still the most expensive routine
   requests at scale**, and they grow fastest.
   - Since 7.13 they are counted in SQL and only the page comes back, but each
     request still reads every token in the selection.
   - Word frequency grows linearly too, from a much lower base.
   - KWIC's non-default sorts are bounded by a 10,000-match cap. That is why
     they are cheap today (the most common real word, *saya*, has fewer
     matches than the cap) but flat at ~1.9 s once a word passes it.
4. **Indexing is Python-bound, not database-bound.** The whole DB side of a
   document costs 10 ms. Worker throughput is a function of CPU cores, so
   scale worker *replicas*, not disk.

### Hot working set

What Postgres needs resident for warm-path performance (token heap + token
indexes + wordfreq + wordtype) is **~0.23 KB per source word**, about 8.5 MB
today. That is the RAM target in §7. It was ~0.37 KB per word while the n-gram
table's index was part of it.

---

## 5. Do the derived tables earn their storage?

The space-for-speed trade is the right *idea*, but it is an empirical claim,
so it was measured: each query was run against its derived table and then
re-run computing the same answer from the source table, both warm.

| Derived table | Share of corpus data | Query | From derived table | Computed from source | Speedup |
| --- | --- | --- | --- | --- | --- |
| `main_token` | 16% | 2-word KWIC, **rare** term *(two-mode run)* | **0.43 ms** | 32 ms *(content scan)* | **75×** |
| `main_token` | 16% | 2-word KWIC, **common** term, paginated count *(two-mode run)* | 237 ms | **30 ms** *(content scan)* | **0.13×** |
| `main_documentwordfreq` | 10% | corpus word frequency, top 50 *(real corpus)* | 6.2 ms | 10.0 ms *(from `main_token`)* | **1.6×** |
| ~~`main_documentngram`~~ *(dropped in 7.13)* | was 62% | corpus bigram page, top 50 *(real corpus, end to end)* | 144 ms | **39 ms** *(`lead()` over `main_token`, §3)* | **0.27×** |

So the honest answer was **one does, one barely, and the largest didn't at
all**, which is why the largest is gone:

- **`main_token` earns its storage decisively** — but only for selective
  queries. Its cost scales with the number of *matches*, while a content
  scan scales with corpus *size*. For a rare word (which is what corpus
  linguistics actually searches for) it is 75× faster; for a very common
  word it is 8× *slower*, because the index has to walk every occurrence.
  It is also not really optional: a content scan cannot produce positions,
  and it matches substrings.
- **`main_documentwordfreq` barely earns it.** 2.9 MB buys 4 ms on a 10 ms
  query. Worth keeping while that is true, and worth re-measuring at catalogue
  size before deciding anything.
- **`main_documentngram` didn't earn it at all.** 62% of the corpus data made
  n-gram pages **slower** than computing them from `main_token` in one SQL
  pass, and it miscounted long documents. Dropped in 7.13 (§3).

### Why the trade is so weak here

Precomputed aggregates pay off when they *collapse* many rows into few. At a
127-word mean document length they collapse almost nothing:

- `main_documentwordfreq` holds 20,100 rows against `main_token`'s 37,758:
  47% fewer, the best ratio here, because real learners reuse words within an
  essay. Even so, summing per-document counts is only slightly cheaper than
  counting tokens.
- `main_documentngram` was **96.9% `count = 1`** rows. It collapsed nothing;
  it only added a second copy of the data to read.

**These aggregates are keyed per document, and a document here is too short for
per-document aggregation to mean anything.** Repetition only appears across
documents. That is why n-grams are now computed from the token stream (§3),
and why §8 treats a corpus-level rollup as the real scaling work.

---

## 6. Fixed, corpus-independent memory

These do not grow with the corpus and form the floor of every tier.

| Component | Resident | Note |
| --- | --- | --- |
| gunicorn (Django) | ~200 MB per worker | **Currently 1 worker** — see §9. |
| uvicorn (FastAPI) | ~250 MB per worker × 4 | async; one worker serves many concurrent requests |
| Celery worker | ~250 MB per container | **1 task at a time** — see §9. |
| Celery beat | ~200 MB *(estimated: same image as the worker)* | exactly one instance |
| Redis (`kuis-redis`) | ≤256 MB | broker and result backend. No `maxmemory` is set, so this is an expectation, not a cap |
| Redis (`kuis-cache`) | 64–128 MB, hard cap | **proposed, deferred**. See `docs/caching-plan.md` §7 |
| KUIS-FE `next start` | ~350 MB | |
| nginx-gateway | ~50 MB | shared across all stacks on the VPS |
| OS + Docker | ~700 MB | |
| **Floor** | **≈ 3.0 GB** | before a single corpus byte is cached (≈ 3.1 GB with the proposed cache) |

`next build` transiently needs **2–3 GB**. On a 4 GB box it will OOM while
the rest of the stack is running. Build in CI and ship an image, or add swap.

---

## 7. Tiers

T1 and above use the catalogue's 124-word mean. Postgres is sized at the
measured ~0.3 KB/word plus ~10 MB fixed (§3), now that the n-gram table is
gone. Disk is provisioned at ~2.5× the
database for WAL, autovacuum bloat and a local dump, rounded to a sane volume
size.

| | **T0 Pilot** *(today)* | **Catalogue complete** | **T1 Department** | **T2 Multi-university** | **T3 National** |
| --- | --- | --- | --- | --- | --- |
| Named users | ≤ 25 | ≤ 25 | ≤ 100 | ≤ 500 | ≤ 2,000 |
| Concurrent users | ≤ 5 | ≤ 5 | ≤ 10 | ≤ 40 | ≤ 150 |
| Documents | **298** | 1,559 | 10,000 | 80,000 | 400,000 |
| Source words | 0.04 M | 0.19 M | 1.24 M | 9.9 M | 49.6 M |
| Raw text | ~0.24 MB | ~1.2 MB | ~8 MB | ~62 MB | ~310 MB |
| **Postgres size** | **21 MB** *(measured)* | **~70 MB** | **~0.4 GB** | **~3 GB** | **~15 GB** |
| … with the old n-gram table, for reference | 41 MB *(measured)* | 167 MB *(measured, synthetic)* | ~1.0 GB *(0.93 GB measured at 1.16 M words)* | ~8 GB | ~40 GB |
| Hot set (§4) | 9 MB | 0.04 GB | 0.3 GB | 2.3 GB | 11 GB |
| Token rows | 0.04 M | 0.19 M | 1.24 M | 9.9 M | 49.6 M |
| Result cache (`kuis-cache`, if built) | — | — | 128 MB | 256 MB | 512 MB |

### Recommended hardware

| | **T0** (and catalogue complete) | **T1** | **T2** | **T3** |
| --- | --- | --- | --- | --- |
| Topology | single VPS | single VPS | app node + DB node | 2 app nodes + DB + read replica |
| vCPU | 2 (4 comfortable) | 4 | 4 app / 8 DB | 8 ×2 app / 16 DB |
| RAM | 4 GB (8 GB comfortable) | 8 GB | 8 GB app / 16 GB DB | 16 GB ×2 app / 32 GB DB |
| Disk | 40 GB NVMe | 80 GB NVMe | 60 GB app / 250 GB NVMe DB | 60 GB ×2 / 1 TB NVMe |
| `shared_buffers` | 1 GB | 2 GB | 4 GB | 8 GB |
| `effective_cache_size` | 2 GB | 5 GB | 12 GB | 24 GB |
| gunicorn workers | 3 | 5 | 9 | 17 (per node) |
| uvicorn workers | 2 | 4 | 8 | 8 (per node) |
| Celery replicas | 1 | 2 | 4 | 8 |

**T0 is where the project is today: 298 documents, 21 MB of Postgres.** A
2 vCPU / 4 GB / 40 GB VPS is generous. The whole database fits in RAM many
times over. Annotating the rest of the catalogue (1,559 documents, ~70 MB)
doesn't change that. The step from 4 GB to 8 GB buys headroom for `next build`,
not for the data.

**T1** still fits one box comfortably: 0.3 GB hot set against 8 GB of RAM
means effectively every query runs on the warm path.

**T2** is where Postgres should move off the app node. The ~2.3 GB hot set
wants RAM that isn't shared with the app workers under 40 concurrent requests,
and §8's ceiling bites here.

**T3** assumes the §8 rollup work has been done. Without it, one n-gram page
takes over a minute even with the 7.13 `lead()` query, and no amount of
hardware fixes that.

The DB-node RAM and Postgres memory settings at T2/T3 were halved on 2026-10-19,
along with the hot set they are sized against. They were 32 GB / 64 GB, sized
for 8.9 GB / 45 GB hot sets measured before `0017`. Disk was left as
provisioned: it already had far more than 2.5× headroom. Dropping the n-gram
table in 7.13 then roughly halved the hot set and the database again. The
hardware rows were deliberately left as they are, so they now carry about 2×
headroom at T2/T3. Re-size them from a measurement when T2 is actually
planned.

---

## 8. The real ceiling is architectural, not hardware

Every ranked feature reads all the selected documents' rows at query time, so
its cost is linear in the number of words selected. Measured at three sizes,
then extrapolated linearly from T1:

| Tier | Words | N-grams page, n = 5, before 7.13 | Since 7.13 (`lead()`, §3) | Word frequency page |
| --- | --- | --- | --- | --- |
| T0 *(today, real)* | 0.04 M | 0.15 s *(measured)* | 0.05 s *(measured)* | 0.03 s *(measured)* |
| Catalogue | 0.19 M | 0.76 s *(measured)* | 0.38 s *(measured)* | 0.12 s *(measured)* |
| T1 | 1.16 M | 4.25 s *(measured)* | 1.86 s *(measured)* | 0.61 s *(measured)* |
| T2 | 9.9 M | ~36 s | ~16 s | ~5 s |
| T3 | 49.6 M | ~3 min | ~80 s | ~26 s |

**The current design — every selected document's rows read at query time —
holds to roughly T1 and fails at T2.** The 7.13 `lead()` query bought about 2×
and made the counts exact, but left the curve linear. Past ~1–2 M words the fix is a
corpus-level rollup (a `CorpusWordFreq` / `CorpusNgram` table or a materialized
view maintained by the worker), not a bigger server.

One constraint on that rollup: **a metadata filter resolves to an arbitrary set
of documents**, which no precomputed per-corpus table can answer. A rollup
serves whole-corpus selections, which are the common case. Filtered requests
fall back to computing from `main_token` with `lead()`. Designing that split is
the T2 work.

**Caching does not move this ceiling.** The proposed Phase 8 cache
(`docs/caching-plan.md`) makes a *repeated* request cost milliseconds. The
first request for every page, sort and filter still costs what the table above
shows. A cache buys time at T1; it does not make T2 work.

Budget this work *before* provisioning T2 hardware. It is the cheaper half
of the problem.

---

## 9. Two deployment defects that affect capacity

Both are in the shipped images and both change the capacity maths, so they
belong here rather than only in a code review.

### `docker/worker.Dockerfile` — `--concurrency=4` on the broken prefork pool

The image is `python:3.14-slim` and the command uses Celery's default prefork
pool, which does not work on this Python version — tasks die before their
body runs. The worker must run `--pool=solo`:

```dockerfile
CMD ["celery", "-A", "worker.celery_app", "worker", "--loglevel=info", "--pool=solo"]
```

`--pool=solo` means **one task at a time per container**. Worker capacity
therefore scales by container replicas, not by `--concurrency`, which is why
§7 lists Celery replicas rather than a concurrency number.

### `docker/django.Dockerfile` — gunicorn with no `--workers`

```dockerfile
gunicorn config.wsgi:application --bind 0.0.0.0:9415
```

gunicorn defaults to **one synchronous worker**, so Django serves exactly one
request at a time. At the "≤ 10 concurrent users" of T1 that is a hard
bottleneck regardless of how much CPU the box has. Set it explicitly:

```dockerfile
CMD ["sh", "-c", "... && gunicorn config.wsgi:application --bind 0.0.0.0:9415 --workers ${GUNICORN_WORKERS:-3} --timeout 120"]
```

Use `2 × cores + 1`, matching the §7 table.

---

## 10. Bulk ingest

The DB side of indexing one document is 10 ms (measured, two-mode run). The
rest is Python tokenization, `Counter` passes over 4 n-gram streams, and Django
model instantiation for ~700 objects.

**Measured** end-to-end throughput is **~20–23 documents per second**:

- the real corpus: 298 documents in 12.8 s;
- synthetic: 1,559 in 80 s, then 7,795 more in 350 s.

Each run was `index_document` → `aggregate_document` in one process with
Celery in eager mode, so there was no broker round trip, on an Apple M2. A VPS
vCPU is usually slower per core, so plan on ~20 documents per second or below.
The throughput above was measured while the worker still wrote n-grams.
Dropping them in 7.13 removed about two thirds of the rows each document writes,
so ingest should now be faster (**estimated**, not measured).

At 20 documents per second:

| Tier | Documents | 1 worker | 4 workers |
| --- | --- | --- | --- |
| Catalogue complete | 1,559 | ~1.3 min | — |
| T1 | 10,000 | ~8 min | ~2 min |
| T2 | 80,000 | ~67 min | ~17 min |
| T3 | 400,000 | ~5.5 h | ~1.4 h |

Initial loads are the only time worker replicas matter much; scale them up
for the migration and back down afterwards.

---

## 11. Summary

- The corpus is **text, and text is small**: 298 essays, 37,758 words, 237 KB
  today. Postgres holds 21 MB, ~45× the text.
- **~0.3 KB of Postgres per source word** plus ~10 MB fixed; **~0.23 KB per
  word** of that wants to stay in RAM.
- **The per-document n-gram table is gone (7.13)**; n-grams are counted from
  `main_token` with `lead()` (§3). That measured 2–3.7× faster at every size,
  exact where the table undercounted, and it freed 62% of corpus storage.
  Raising the floor to 2 was rejected: it kept 0.7–20% of occurrences and got
  almost none of the top 100 right.
- `main_token` clearly pays for itself: 75× on selective queries, and it is
  required for correctness anyway. `main_documentwordfreq` barely does (§5).
- Today's corpus, and the full catalogue, need a **2 vCPU / 4 GB / 40 GB NVMe
  VPS**. It would still fit comfortably at 10,000 documents on 4 vCPU / 8 GB.
- Fix the worker's pool and gunicorn's worker count (§9) before concluding
  the hardware is too small — at T0 and T1 it almost certainly isn't.
- The result cache is **not justified today** (`docs/caching-plan.md` §1). If
  it is built later, it adds one container capped at 64–128 MB and no disk.
- Past ~1–2 M words, buy a **corpus-level aggregate rollup** (§8), not a
  bigger server, and not a cache, which only speeds up repeats.
