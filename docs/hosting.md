# Hosting and capacity planning

How much server KUIS (this repo) and KUIS-FE need, and when each tier runs
out. Every number marked **measured** came from loading a synthetic corpus
with the *real* per-document word-count distribution from `metadata/*.csv`
into a throwaway `postgres:16` and reading `pg_total_relation_size` /
`EXPLAIN (ANALYZE, BUFFERS)`. Numbers marked **estimated** are derived and
flagged as such — don't treat them as the same kind of fact.

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
| `kuis-redis` | `redis:7-alpine` | Celery broker only. |
| KUIS-FE | Next.js 16 / React 19, `next start` | SSR frontend. No Dockerfile in the repo yet. |

---

## 2. Assumptions

Stated up front because every tier below is just these numbers multiplied.

### Corpus shape — measured from `metadata/*.csv` (n = 1,559 catalogue rows)

| Property | Value |
| --- | --- |
| Documents today | 1,559 |
| Total words today | 193,813 |
| Mean words/document | **124** |
| Median words/document | 94 |
| p90 / max | 301 / 650 |
| Stored XML size | ~15.7 bytes per word (measured on `info/*.xml`) |
| Error annotations | ~0.10 per word (47 segments / 487 words, `info/KUIS2022NAMI3.xml`) |

**These are short student essays, not books.** The entire corpus today is
about **1.2 MB of plain text**. Sizing this system in "GB of corpus" is
misleading: the raw text is negligible and the *derived* tables are
everything. All tiers below are therefore stated in documents and words,
with a raw-text GB figure given only for translation.

### Derived-row multipliers — measured

The worker writes, per source word:

| Table | Rows per source word | Why |
| --- | --- | --- |
| `main_token` | **2.00** | one row per word × 2 modes (original + corrected) |
| `main_documentwordfreq` | **1.55** | complete per-document type counts × 2 modes |
| `main_documentngram` | **7.81** | n = 2,3,4,5 × 2 modes, `TOP_K=500`, `FREQUENCY_FLOOR=1` |
| `main_errorannotation` | 0.10 | one per `<segment>` |

### Workload

- Reads dominate. Writes happen in bursts at bulk ingest, then go quiet.
- An active user fires an analysis query roughly every 20–30 s while working.
- "Concurrent" below means genuinely in-flight requests, not logged-in sessions.
  A ~5–10% concurrent-to-named ratio is assumed.
- Single region, single AZ. No HA requirement below Tier 3.
- Backups: nightly `pg_dump` kept locally plus shipped off-box.

---

## 3. Measured storage, at today's corpus

Real corpus distribution, all derived tables populated, after `VACUUM ANALYZE`:

| Table | Rows | Heap | Indexes | Total | Bytes/row |
| --- | --- | --- | --- | --- | --- |
| `main_documentngram` | 1,514,655 | 162 MB | 58 MB | **220 MB** | 152 |
| `main_token` | 387,626 | 25 MB | 36 MB | **62 MB** | 166 |
| `main_documentwordfreq` | 300,400 | 20 MB | 32 MB | **52 MB** | 181 |
| `main_errorannotation` | 18,694 | 3.3 MB | 1.4 MB | 4.8 MB | 265 |
| `main_wordtype` | 22,000 | 1.1 MB | 1.4 MB | 2.6 MB | 120 |
| `main_document` | 1,559 | 1.7 MB | 0.2 MB | 2.0 MB | 1,303 |
| **Whole database** | | | | **350 MB** | |

Two constants fall out of this, and they drive every tier:

- **1.9 KB of Postgres per source word**
- **230 KB of Postgres per document** (at the 124-word mean)

That is a ~**290×** blow-up over the raw text. `main_documentngram` alone is
**63%** of the database.

### Why the n-gram table is so large

With `FREQUENCY_FLOOR = 1` and a 124-word document, nearly every n-gram in
that document occurs exactly once, so top-500 never truncates anything —
the table stores ~7.8 rows per source word. Measured: **99.8% of all n-gram
rows have `count = 1`**. Raising the floor to 2 would shrink the database
from 350 MB to ~130 MB.

That is *not* a free win, and shouldn't be taken as a recommendation on its
own: at these document lengths, dropping `count = 1` leaves per-document
n-gram analysis with almost nothing to show. The real observation is that
**per-document n-gram storage is the wrong shape for short texts** —
repetition only appears when you aggregate across documents. A corpus-level
n-gram rollup would be both smaller and more meaningful. Until that exists,
budget for 63% of the database being n-grams. See §8.

---

## 4. Measured query cost, at today's corpus

| Query | Cold | Warm |
| --- | --- | --- |
| 2-word KWIC (self-join on `main_token`) | **338 ms** | **50 ms** |
| 3-word KWIC, uncommon first term | — | 0.8 ms |
| Whole-corpus word frequency (`SUM … GROUP BY word_type_id`) | — | **411 ms** |
| One document's full write path (248 + 155 + 968 rows) | — | **10 ms** |

Three conclusions:

1. **Cold vs. warm is a 6.7× difference on KWIC.** The cold run read 2,410
   heap pages (~19 MB of random I/O). Sizing RAM so Postgres keeps the token
   indexes resident is the single highest-value decision here — and the disk
   must be NVMe, never network HDD.
2. **The frequency aggregate is a full scan** of `main_documentwordfreq` and
   scales linearly with corpus size. At 411 ms today it is already the most
   expensive routine query, and it does not benefit from a warm cache the
   way KWIC does. This sets the ceiling in §8.
3. **Indexing is Python-bound, not database-bound.** The whole DB side of a
   document costs 10 ms. Worker throughput is a function of CPU cores, so
   scale worker *replicas*, not disk.

### Hot working set

What Postgres needs resident for warm-path performance (token heap + token
indexes + wordfreq + wordtype + n-gram index) is **~0.9 KB per source word**
— roughly half the total database. That is the RAM target in §7.

---

## 5. Do the derived tables earn their storage?

The space-for-speed trade is the right *idea*, but it is an empirical claim,
so it was measured: each aggregate query was run against its derived table
and then re-run computing the same answer from the source table, both warm.

| Derived table | Share of DB | Query | From derived table | Computed from source | Speedup |
| --- | --- | --- | --- | --- | --- |
| `main_token` | 18% | 2-word KWIC, **rare** term | **0.43 ms** | 32 ms *(content scan)* | **75×** |
| `main_token` | 18% | 2-word KWIC, **common** term, paginated count | 237 ms | **30 ms** *(content scan)* | **0.13×** |
| `main_documentwordfreq` | 15% | corpus word frequency, top-100 | 45 ms | 53 ms *(from `main_token`)* | **1.2×** |
| `main_documentngram` | 63% | corpus bigram frequency, top-100 | 255 ms | 463 ms *(from `main_token`)* | **1.8×** |

So the honest answer is **partly, and less than you would expect**:

- **`main_token` earns its storage decisively** — but only for selective
  queries. Its cost scales with the number of *matches*, while a content
  scan scales with corpus *size*. For a rare word (which is what corpus
  linguistics actually searches for) it is 75× faster; for a very common
  word it is 8× *slower*, because the index has to walk every occurrence.
  It is also not really optional: a content scan cannot produce positions,
  cannot separate original from corrected mode, and matches substrings.
- **`main_documentwordfreq` does not earn its storage.** 52 MB buys 8 ms.
- **`main_documentngram` earns it poorly.** 220 MB — 63% of the entire
  database — buys 1.8×.

**78% of the database buys between 1.2× and 1.8×.**

### Why the trade is so weak here

Precomputed aggregates pay off when they *collapse* many rows into few. At a
124-word mean document length they collapse almost nothing:

- `main_documentwordfreq` holds 300,400 rows against `main_token`'s 387,626
  — only **22% fewer**. A type/token ratio of 0.775 means almost every word
  in a document occurs exactly once there, so summing per-document counts is
  barely cheaper than counting the tokens.
- `main_documentngram` is **99.8% `count = 1`** rows, for the same reason.

This is the same observation as §3, from the performance side rather than
the storage side: **these aggregates are keyed per document, and a document
here is too short for per-document aggregation to mean anything.** Rolling
them up per *corpus* would make them both much smaller and much faster,
which is why §8 treats that as the real scaling work.

None of this argues for deleting the tables today — at T0 and T1 the
absolute numbers are all small, and `main_token` is load-bearing. It argues
that the derived tables should not be assumed to be buying performance that
justifies their size, and that the rollup in §8 is the change that would
make the trade a good one.

---

## 6. Fixed, corpus-independent memory

These do not grow with the corpus and form the floor of every tier.

| Component | Resident | Note |
| --- | --- | --- |
| gunicorn (Django) | ~200 MB per worker | **Currently 1 worker** — see §9. |
| uvicorn (FastAPI) | ~250 MB per worker × 4 | async; one worker serves many concurrent requests |
| Celery worker | ~250 MB per container | **1 task at a time** — see §9. |
| Redis | ≤256 MB | broker only |
| KUIS-FE `next start` | ~350 MB | |
| nginx-gateway | ~50 MB | shared across all stacks on the VPS |
| OS + Docker | ~700 MB | |
| **Floor** | **≈ 2.8 GB** | before a single corpus byte is cached |

`next build` transiently needs **2–3 GB**. On a 4 GB box it will OOM while
the rest of the stack is running. Build in CI and ship an image, or add swap.

---

## 7. Tiers

Words derived at the measured 124-word mean. Postgres sized at the measured
1.9 KB/word; disk provisioned at ~2.5× the database for WAL, autovacuum
bloat and a local dump, rounded to a sane volume size.

| | **T0 Pilot** | **T1 Department** | **T2 Multi-university** | **T3 National** |
| --- | --- | --- | --- | --- |
| Named users | ≤ 25 | ≤ 100 | ≤ 500 | ≤ 2,000 |
| Concurrent users | ≤ 5 | ≤ 10 | ≤ 40 | ≤ 150 |
| Documents | 1,600 *(today)* | 10,000 | 80,000 | 400,000 |
| Source words | 0.19 M | 1.24 M | 9.9 M | 49.6 M |
| Raw text | ~1.2 MB | ~8 MB | ~62 MB | ~310 MB |
| **Postgres size** | **0.35 GB** *(measured)* | **2.3 GB** | **19 GB** | **94 GB** |
| Hot set (§4) | 0.17 GB | 1.1 GB | 8.9 GB | 45 GB |
| Token rows | 0.39 M | 2.5 M | 20 M | 99 M |
| N-gram rows | 1.5 M | 9.7 M | 77 M | 387 M |

### Recommended hardware

| | **T0** | **T1** | **T2** | **T3** |
| --- | --- | --- | --- | --- |
| Topology | single VPS | single VPS | app node + DB node | 2 app nodes + DB + read replica |
| vCPU | 2 (4 comfortable) | 4 | 4 app / 8 DB | 8 ×2 app / 16 DB |
| RAM | 4 GB (8 GB comfortable) | 8 GB | 8 GB app / 32 GB DB | 16 GB ×2 app / 64 GB DB |
| Disk | 40 GB NVMe | 80 GB NVMe | 60 GB app / 250 GB NVMe DB | 60 GB ×2 / 1 TB NVMe |
| `shared_buffers` | 1 GB | 2 GB | 8 GB | 16 GB |
| `effective_cache_size` | 2 GB | 5 GB | 24 GB | 48 GB |
| gunicorn workers | 3 | 5 | 9 | 17 (per node) |
| uvicorn workers | 2 | 4 | 8 | 8 (per node) |
| Celery replicas | 1 | 2 | 4 | 8 |

**T0 is where the project is today.** A 2 vCPU / 4 GB / 40 GB VPS genuinely
suffices — the whole database fits in page cache with room to spare. The
step to 4 GB → 8 GB buys headroom for `next build` and for the Postgres
cache to stay warm under concurrent use.

**T1** still fits one box comfortably: 1.1 GB hot set against 8 GB of RAM
means effectively every query runs on the warm path.

**T2** is where Postgres should move off the app node. The 8.9 GB hot set
needs a machine whose RAM is dedicated to it, and §8's ceiling bites here.

**T3** assumes the §8 rollup work has been done. Without it, the frequency
aggregate alone takes ~100 s and no amount of hardware fixes that.

---

## 8. The real ceiling is architectural, not hardware

The whole-corpus frequency aggregate is a linear full scan of
`main_documentwordfreq` (411 ms measured at 0.19 M words). Extrapolating:

| Tier | Words | Frequency aggregate (estimated) |
| --- | --- | --- |
| T0 | 0.19 M | 0.41 s *(measured)* |
| T1 | 1.24 M | ~2.6 s |
| T2 | 9.9 M | ~21 s |
| T3 | 49.6 M | ~105 s |

**The current Tier-2 design — aggregates stored per document and summed at
query time — holds to roughly T1 and fails at T2.** Past ~1–2 M words the
fix is a corpus-level rollup (a `CorpusWordFreq` table or a materialized
view maintained by the worker), not a bigger server. The same argument
applies to the n-gram table, which would shrink dramatically and become more
useful in rolled-up form (§3).

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

The DB side of indexing one document is 10 ms (measured); the rest is Python
tokenization, `Counter` passes over 8 n-gram streams, and Django model
instantiation for ~1,370 objects. **Estimated** throughput is therefore
roughly **20–50 documents/second per worker container** — this one is a
derivation, not a measurement, and should be timed against the real task
before anyone plans a migration window on it.

At that rate, and taking the pessimistic end:

| Tier | Documents | 1 worker | 4 workers |
| --- | --- | --- | --- |
| T1 | 10,000 | ~8 min | ~2 min |
| T2 | 80,000 | ~67 min | ~17 min |
| T3 | 400,000 | ~5.5 h | ~1.4 h |

Initial loads are the only time worker replicas matter much; scale them up
for the migration and back down afterwards.

---

## 11. Summary

- The corpus is **text, and text is small** — 1.2 MB today. The derived
  tables are ~290× that, and n-grams are 63% of them.
- **1.9 KB of Postgres per source word**; **0.9 KB per word** of that wants
  to stay in RAM.
- The derived tables trade storage for speed, but measured, **78% of the
  database buys only 1.2–1.8×** (§5). `main_token` is the one that clearly
  pays — 75× on selective queries — and it is required for correctness
  anyway.
- Today's corpus needs a **2 vCPU / 4 GB / 40 GB NVMe VPS**, and would still
  fit comfortably at 10,000 documents on 4 vCPU / 8 GB.
- Fix the worker's pool and gunicorn's worker count (§9) before concluding
  the hardware is too small — at T0 and T1 it almost certainly isn't.
- Past ~1–2 M words, buy a **corpus-level aggregate rollup** (§8), not a
  bigger server.
