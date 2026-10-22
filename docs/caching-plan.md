# Caching plan (Phase 8 / Tier 4)

**Status: designed, deferred, not implemented.** This doc is the evidence and the
design for a server-side result cache. Its conclusion (§1) is that the cache is
**not justified yet**. It should be built when one of the triggers in §1.3
fires, and not before. Nothing here exists in the code.

Every number marked **measured** came from the benchmark in §2. Numbers marked
**estimated** are derived and flagged as such. Same convention as
`docs/hosting.md`.

---

## 1. Is the cache justified?

### 1.1 Verdict: not today, probably not at catalogue size, yes at T1

A server-side cache pays for itself only when all four of these hold:

| Test | Today (real corpus: 298 docs, 0.04 M words) | Catalogue complete (1,559 docs, 0.19 M) | T1 (≈ 10,000 docs, 1.2 M) |
| --- | --- | --- | --- |
| **1. A miss is slow enough for users to notice** (≳ 0.5–1 s) | ✗ The slowest request is **111 ms** (KWIC `sort=left`); most are under 55 ms | ~ 1.8 s on KWIC's non-default sorts; n-gram and collocation pages 0.2–0.4 s. Under ~0.4 s throughout once the KWIC sort fix lands | ✓ 1–2 s even after the query fixes; 2–4 s before them |
| **2. Requests actually repeat** | ~ Unknown. Shared landing views and classroom use would repeat; TanStack Query already serves same-browser repeats for 5 min | same | same |
| **3. No cheaper fix exists** | ✗ The query fixes (§8) are cheaper and make *first* requests faster too | ✗ same | ~ The query fixes help ~2×, but the cost stays linear |
| **4. The cost is low** | ✓ One container, ≤ 128 MB, no disk | ✓ | ✓ |

**Today it fails tests 1 and 3.** A cache hit would save at most ~110 ms, on a
request nobody perceives as slow. Meanwhile it adds:

- a container to deploy;
- invalidation hooks in the worker;
- a new staleness failure mode (§6.5);
- about ten tests.

**At catalogue size it is borderline before the query fixes and unjustified
after them.** The n-gram fix has shipped (`docs/hosting.md` §3): it
measures 0.22–0.38 s for the worst ranked page at that size. The KWIC sort fix
should bring `sort=left|right|document` near the default sort's cost
(**estimated**).

**At T1 it is justified.** Even after the query fixes, n-gram pages measure
1.1–1.9 s. A shared result cache is then the cheapest way to make repeated
views instant, until the corpus-level rollup in `docs/hosting.md` §8 exists.

### 1.2 What to do instead, now

1. **Enforce private corpora on the data plane (§6.1).** This is a security gap,
   independent of caching, and it is the prerequisite for any shared cache
   later.
2. ✅ **Done in 7.13:** the n-gram/collocation read path is a `lead()` query
   over `main_token`, and `DocumentNgram` is dropped (`docs/hosting.md` §3).
3. **Sort KWIC `left|right|document` in SQL** (§8). This removes the
   10,000-window load.
4. **Keep this design on file.** Nothing in it goes stale while it waits. It
   keys on resolved document IDs, so new metadata facets don't affect it.

### 1.3 Triggers for building it

Build the cache when **any** of these is true:

- production request logs show an analysis endpoint's **p95 above ~1 s**;
- the corpus passes **~0.5 M words** (about 4,000 documents, roughly 2.5× the
  complete catalogue), where the measured curves put n-gram pages above
  ~0.8 s even with `lead()` (**estimated**, by linear interpolation between the
  catalogue and T1 measurements);
- a usage pattern appears that is explicitly repeated and shared: a course in
  which every student opens the same whole-corpus view.

Before building, re-run §3's measurements on the corpus as it is then, because
the case rests on those numbers.

---

## 2. How the numbers were obtained

**Today's numbers are the real corpus.** The 298 annotated XML files in
`Koper/Results/Eror-XML` were loaded through the real `get_or_create_document` →
`index_document` → `aggregate_document` path, with the real taxonomy, metadata
catalogue and author imports, into a throwaway database (its name contained
`test`).

**Catalogue size (1,559 documents) and T1 (9,354 documents) are synthetic.**
Each document is generated at one `metadata/*.csv` row's real word count, with
a Zipfian vocabulary (25,000 types, s = 1) and one error `<segment>` per ~10
words using real taxonomy codes. Each is indexed through the same tasks.

Each feature was called through its real Service class (not raw SQL), several
times after one warm-up run, and the median taken. Entry sizes are
`MEMORY USAGE` from a real Redis 8 instance; hit times are loopback round trips.
Everything ran on Postgres 18.1 on an Apple M2. Production runs `postgres:16`
on VPS vCPUs, so read the timings as relative.

| Scale | Documents | Words |
| --- | --- | --- | --- |
| **Today (real)** | 298 | 37,758 |
| Catalogue complete (synthetic) | 1,559 | 193,813 |
| T1 (synthetic) | 9,354 | 1,162,878 |

Every request below uses the **whole corpus** as its scope, the worst case.
Synthetic text repeats less than real learner text, so synthetic distinct
n-gram counts, and the full-list sizes in §4, are upper bounds.

---

## 3. Where the time goes

**Measured**, warm, whole-corpus scope.

| Request | Today (real) | Catalogue | T1 | Final page in Redis |
| --- | --- | --- | --- | --- |
| N-grams, n = 3 / n = 5 | 44 / 53 ms | 285 / 378 ms † | **1,557 / 1,856 ms** † | 2.0–3.0 KB |
| Collocations | 37 ms | 217 ms † | **1,284 ms** † | 2.0 KB |
| KWIC, most common word, `sort=left` | 111 ms | **1,784 ms** | **1,936 ms** | 7.5–8.0 KB |
| Word frequency, page 1 / page 2 | 25 / 27 ms | 124 / 124 ms | 607 / 628 ms | 1.5–2.0 KB |
| Word frequency, `q` filter | 27 ms | 90 ms | 406 ms | 2.0 KB |
| Error frequency | 13 ms | 35 ms | 148 ms | 2.0 KB |
| KWIC, most common word, default sort | 14 ms | 33 ms | 114 ms | 8.5–9.5 KB |
| KWIC, mid-frequency word, default sort | 6 ms | 19 ms | 79 ms | 5.5–9.5 KB |
| EPIC | 11 ms | 21 ms | 88 ms | 13.5–14.0 KB |
| Error summary | 5 ms | 9 ms | 47 ms | 1.5–6.5 KB |
| Metadata facets (global) | 2 ms | 2 ms | 2 ms | 0.2 KB |
| Taxonomy tree (global) | 1 ms | 1 ms | 1 ms | 12.0 KB |
| `resolve_document_ids` alone | 1.0 ms | 2.2 ms | 12.8 ms | — (not cached; runs on every request) |
| **A cache hit** | **≈ 1.2 ms** | **≈ 2.5 ms** | **≈ 13 ms** | lookup + one Redis `GET` (0.10–0.31 ms) |

† The n-gram query alone, without the Service's document-id lookup.

At today's size a hit saves 4–110 ms per request. That's the number behind
§1's "not justified".

Four paths account for nearly all of the cost at larger sizes, and each is
slow for a specific, fixable reason:

- **N-grams and collocations.** Still the costliest at scale. The `lead()`
  query sorts in SQL and returns only the page, but it reads every token in the
  selection.
- **KWIC `sort=left|right|document`.** `PostgresKWICRepository.search` fetches up
  to `MAX_UNPAGINATED_MATCHES` (10,000) matches, loads every one's context
  window in one large `OR` query, sorts in Python, then slices.
  - It is cheap today because the most common real word has fewer matches than
    the cap.
  - It is flat at ~1.8–1.9 s once a word passes the cap.
- **Word frequency.** `paginate_sql_source` runs the full `GROUP BY` three times
  (`type_count`, `result_count`, then the page), plus a `token_total` query.
- **Everything else** stays under 150 ms even at T1.

Page 2 costs exactly what page 1 costs on every feature. Pagination never reuses
work.

---

## 4. What to cache: the final page, or the full ranked list

Caching the **full pre-pagination list** (every word or n-gram with its count)
would turn page, sort and filter changes into hits too. It was measured and
rejected:

| Full list, whole corpus | Items (today / T1) | In Redis, zlib-compressed (today / T1) | Fetch + decompress + parse (today / T1) |
| --- | --- | --- | --- |
| Word frequency | 4,813 / 24,951 | 28 KB / 160 KB | 1 ms / 4 ms |
| Bigrams | 21,644 / 649,815 | 160 KB / 5.1 MB | 4 ms / 111 ms |
| Trigrams | 30,844 / 1,052,022 | 320 KB / 10.5 MB | 17 ms / 197 ms |
| 4-grams | 33,789 / 1,126,041 | 416 KB / 14.2 MB | 7 ms / 248 ms |
| 5-grams | 34,687 / 1,123,996 | 512 KB / 17.3 MB | 8 ms / 293 ms |

Those load times don't include the Python filter, sort and slice that would
still follow. At T1, the size at which a cache becomes justified, a 17 MB entry
takes ~0.3 s just to load and keeps growing. That's the per-document scaling
problem moved into Redis, not solved.

**The final page** is 1.5–14 KB on every feature, at every scale. Page size is
capped at 250 rows (`PER_PAGE_CHOICES`), so entry size never grows with the
corpus. This is the design.

---

## 5. Options

| Option | Extra RAM | Disk | New infrastructure | What becomes fast | Main downside |
| --- | --- | --- | --- | --- | --- |
| **A.** Status quo: TanStack Query, 5 min per browser | 0 | 0 | none | the same user repeating a query within 5 min | at T1, n-gram pages take 2–4 s each |
| **B.** In-memory LRU in each uvicorn worker | ×4 copies | 0 | none | ~¼ of repeats (requests round-robin across 4 workers) | still needs a shared invalidation signal; memory sits inside the API processes |
| **C.** Redis, final page | 64–128 MB cap | 0 | one small container | any exact repeat, across users and sessions | a new page, sort or filter is still a miss; data can be stale up to the TTL if a generation bump is lost (§6.5) |
| **D.** Redis, full ranked list | 0.5–1 GB+ at T1 (**estimated**) | 0 | same as C | page, sort and filter changes too | §4: entries too large to beat recomputing at T1, and worse beyond |
| **E.** Fix the slow queries | 0 | − | none | first requests too | engineering time; repeats still cost full price |

**The decision:**

- **E now.** It is the one option that helps every request and reduces cost
  instead of adding any.
- **C later**, when a §1.3 trigger fires.
- **A** remains the client-side layer throughout.

**Why not B:** four processes means four cold caches and a quarter of the hit
rate. Invalidation still needs a shared counter, which means Redis anyway. And
cached results would compete with request memory inside the API processes.

**Why not D:** see §4.

---

## 6. Design, for when it is built (option C)

### 6.1 Prerequisite, and needed now anyway: enforce private corpora on the data plane

`docs/feature-list.md` previously described private corpora as enforced on
"both surfaces". Those two surfaces were the Django HTML views and the Django
REST API. **The FastAPI data plane does not check `Corpus.is_public` at all.**
Every analysis endpoint accepts any `corpus_ids` from any logged-in user,
`resolve_document_ids` never reads the column, and no test covers it.

So today a non-staff user can run every analysis on a private corpus by passing
its ID.

Fix it with one FastAPI dependency that resolves the requested `corpus_ids`
against `is_public` and the token's `is_staff`. Apply it to all seven analysis
endpoints. A private corpus requested by non-staff becomes a **404, not a 403**,
matching the rule on the Django surfaces.

A request that fails the check then never reaches the service, and so never
reaches a cache. **This is not deferred with the cache.**

### 6.2 Where the cache sits

In the **service layer**: a new `dataplane/core/cache.py`, called by each service
after `resolve_document_ids` and wrapped around the repository call.

- Routers, repositories and the repository ABCs don't change.
- The services already own request-shaping (window clamping, `per_page`
  normalisation), so they are where a normalised key can be built.

An illustrative sketch, not final code:

```python
async def cached(feature, document_ids, params, compute, encode, decode):
    if not settings.cache_enabled or not document_ids:
        return await compute()
    try:
        generation = (await client.get(GENERATION_KEY)) or b"0"
        key = make_key(feature, generation, document_ids, params)
        if (raw := await client.get(key)) is not None:
            return decode(json.loads(raw))
    except RedisError:
        logger.warning("cache unavailable; computing", exc_info=True)
        return await compute()

    result = await compute()
    with suppress(RedisError):
        await client.set(key, json.dumps(encode(result)), ex=settings.cache_ttl_seconds)
    return result
```

### 6.3 The key

```
kuis:c:{CACHE_SCHEMA_VERSION}:{generation}:{feature}:{sha1(sorted document ids)}:{sha1(normalised params)}
```

`normalised params` holds every input except corpus and metadata, which are
already captured by the document IDs. Values are taken **after** the service's
own clamping, so equivalent requests share one entry:

| Feature | Params in the key |
| --- | --- |
| Frequency, collocations | `q` (stripped, lowercased), `match`, `sort`, `dir`, `per_page` (normalised), `offset` |
| N-grams | the above + `n` |
| KWIC | the phrase as `query.strip().lower().split()`, `window` (clamped), `sort`, `per_page`, `offset` |
| Error frequency | the frequency params + `codes` (sorted) |
| EPIC | `codes` (sorted), `window` (clamped), `per_page`, `offset` |
| Error summary | none beyond the document IDs |

### 6.4 Why key on the resolved document IDs

The original plan keyed on `(corpus ids, feature, n)`. `docs/CHANGELOG.md`
already notes that this became too narrow: the metadata filter added five
facets, each with a `__none__` sentinel, and forgetting one would serve an
unfiltered result to a filtered request.

Keying on the **output** of `resolve_document_ids` instead of its inputs
removes that whole class of bug:

- **Corpus membership, deleted documents, document status, metadata imports and
  facet values** all reach a feature only by changing which document IDs
  resolve. When they change, the key changes. Nothing has to enumerate them,
  and a facet added later is covered automatically.
- **Different filters that select the same documents share one entry.**

The cost: `resolve_document_ids` runs on every request, hits included. That is
1.0 ms today, 2.2 ms at catalogue size and 12.8 ms at T1 (**measured**).

### 6.5 Invalidation: one generation counter

What the document IDs can't capture is data changing **under the same IDs**: a
document re-tokenized after an edit, or a taxonomy reload. For that, the key
includes a generation number read from Redis. Bumping it makes every existing
entry unreachable at once, and the TTL frees the memory later.

| What changes | How the cache stays correct |
| --- | --- |
| Corpus documents added or removed; corpus deleted | resolved IDs change |
| Document deleted | resolved IDs change |
| Metadata catalogue or author import, `--relink` | resolved IDs change, for every request whose filter is affected |
| `is_public` toggled | rejected before the cache (§6.1) |
| Document re-indexed (an edit, `retokenize`, a `TOKENIZER_VERSION` bump, the hourly stale sweep) | generation bump at the end of `index_document` and `aggregate_document` |
| `load_error_taxonomy` re-run | generation bump at the end of the command |
| A deploy changes a query or a result's shape | bump `CACHE_SCHEMA_VERSION` in code |

There are three bump sites, all on the Django/worker side. Each must run in
`transaction.on_commit`. Bumping before the commit would let a request cache
pre-commit data under the new generation.

The bump helper lives on the Django side (the worker imports Django, the data
plane can't), and the counter's key name is duplicated in the data plane. This
follows the `METADATA_UNSET` precedent in `dataplane/repositories/base.py`:
duplicate one small constant, keep the two copies in step.

Properties worth knowing:

- **Invalidation is global, and that's deliberate.** Writes come in bursts at
  ingest and are quiet otherwise. During a bulk ingest the cache is effectively
  off, which is correct. A race between a slow request and a bump is harmless:
  the request writes its result under the old generation, which nobody reads
  any more.
- **A lost bump means staleness, bounded by the TTL.** If the worker can't reach
  the cache Redis, it logs at ERROR and carries on, and results can be up to one
  TTL stale. That is the one real correctness trade-off in this design, and it
  is part of why the cache shouldn't be built before it earns its keep.

### 6.6 The store: a separate `kuis-cache` container

```yaml
  kuis-cache:
    image: redis:7-alpine
    command: ["redis-server", "--maxmemory", "128mb", "--maxmemory-policy", "allkeys-lru",
              "--save", "", "--appendonly", "no"]
    networks: [app-shared]
```

A new `CACHE_REDIS_URL` variable is read by `kuis-fastapi` (to get and set) and
`kuis-worker` (to bump). Don't add a hard `depends_on`; the cache is optional by
design (§6.7).

**Why not the existing `kuis-redis`:**

- It is the Celery broker **and** result backend (`worker/celery_app.py`), with no
  `maxmemory` configured.
- A cache wants `allkeys-lru`, which on that instance could evict queued
  indexing tasks.
- `noeviction` makes cache writes fail once memory is full.

`volatile-lru` on the shared instance would technically work: nothing reads
Celery results, and every cache key would carry a TTL. But a cache bug or memory
spike would then sit next to the indexing queue. A separate container costs
under 10 MB idle (**estimated**).

### 6.7 Failure behaviour

- **Fail open.** If Redis is unreachable or slow (socket timeout around 50 ms),
  the request computes normally. Losing the cache container costs speed, never
  correctness or availability.
- **No stampede protection.** Two identical concurrent misses both compute. At
  T1 concurrency (≤ 10 in-flight requests) that is cheaper than a locking scheme
  to reason about.
- **JSON, not pickle.** Result dataclasses go through `asdict` on the way in and
  are rebuilt on the way out. Unpickling bytes from a network service is a code
  execution path we don't need.

### 6.8 TTL

**1 hour.** Correctness comes from the generation counter. The TTL only frees
memory and bounds the lost-bump case in §6.5.

### 6.9 Observability

- An `X-Cache: HIT|MISS` response header, set by a small middleware from a
  contextvar that the cache helper writes.
- One log line per request with feature, outcome and duration.

**Add the duration log now, before any cache exists.** It is how the "p95 above
~1 s" trigger in §1.3 gets measured.

### 6.10 Scope

**Cached:** the seven analysis service methods.

- `KWICService.search`
- `FrequencyService.search`
- `CollocationService.search`
- `NgramService.search`
- `ErrorAnalyticsService.frequency`
- `ErrorAnalyticsService.occurrences`
- `ErrorAnalyticsService.summary`

**Not cached:**

- Metadata facets and the taxonomy tree: 1–2 ms, and KUIS-FE caches them
  client-side.
- Anything on Django: the live-parse views retire in Phase 8, and the
  "~6 s → ms" figure in `docs/data-processing.md` §9 describes those views at
  10 M tokens, not the data plane. Retire them, don't cache them.

---

## 7. Resource impact

| | Today / catalogue | T1 | T2 | T3 |
| --- | --- | --- | --- | --- |
| `kuis-cache` `maxmemory` | not built | 128 MB | 256 MB | 512 MB |
| Disk | 0 | 0 | 0 | 0 |
| Containers | 0 | +1 | +1 | +1 |
| CPU | — | lower | lower | lower |
| Change to the recommended hardware in `docs/hosting.md` §7 | none | none | none | none |

The cap scales with **users**, not corpus size, because entries don't grow
(§4). At ~5 KB per entry, 128 MB holds about 25,000 cached pages. The T2/T3 caps
are **estimated**; §6.9's logs are how to check them. For scale, 128 MB is about
1.6% of the 8 GB T1 box.

---

## 8. Query-shape fixes (option E), the work to do first

None of this needs a cache, and all of it helps the first request:

1. ✅ **Done in 7.13: n-grams and collocations are computed from `main_token`
   with `lead()`, and `DocumentNgram` is dropped.**
   - Measured: 39–58 ms today, 217–378 ms at catalogue size, 1.3–1.9 s at T1.
   - The counts are exact.
2. **KWIC `sort=left|right`: get the sort key in SQL.**
   - Join the token at `position - 1` (or `position + n`) to get the adjacent
     word, order by it, and paginate in SQL.
   - Only the page's context windows then need loading, instead of up to 10,000.
   - `sort=document` is a join to `main_document.title`.
3. **Word frequency: one pass instead of three.** `count(*) OVER ()` gives
   `type_count` and `result_count` alongside the page. It matters at T1, not
   today.

These are linear-cost improvements of ~2×. The corpus-level rollup in
`docs/hosting.md` §8 is what changes the curve.

---

## 9. Implementation checklist (when a trigger fires)

| File | Change |
| --- | --- |
| `dataplane/dependencies.py` (+ the 7 analysis routers) | §6.1 visibility dependency. **Do this now, not with the cache** |
| `dataplane/core/config.py` | `cache_redis_url`, `cache_ttl_seconds` (3600), `cache_enabled` |
| `dataplane/core/cache.py` | **new**: async client, `make_key`, `cached()`, the `X-Cache` contextvar |
| `dataplane/main.py` | the `X-Cache` / duration middleware (the duration half now, §6.9) |
| `dataplane/services/*.py` | wrap the 7 repository calls (§6.10) |
| `main/cache_generation.py` | **new**: `bump_cache_generation()`, logs at ERROR on failure |
| `worker/tasks/indexing.py` | `transaction.on_commit(bump_cache_generation)` at the end of `index_document` and `aggregate_document` |
| `main/management/commands/load_error_taxonomy.py` | bump after a load |
| `docker-compose.yml`, `.env` | the `kuis-cache` service, `CACHE_REDIS_URL` |
| `docs/ONBOARDING.md`, `docs/data-processing.md`, `docs/hosting.md`, `docs/CHANGELOG.md` | the new variable, the built design, the service list, the Phase 8 row |

**Tests**, on the data plane's real Postgres plus a Redis (or a fake implementing
`get`/`set`/`incr`):

- the second identical request is served without calling the repository;
- equivalent requests share a key: `q` differing only in case or whitespace, an
  invalid `per_page`, an out-of-range `window`;
- adding a document to a corpus makes the next request a miss with the new
  result;
- a generation bump makes the next request a miss;
- a filtered and an unfiltered request share an entry **only** when they resolve
  to the same documents;
- with Redis unreachable, results are still correct and nothing raises;
- a non-staff user gets 404 for a private corpus, **even when a staff user has
  already cached that exact result**;
- `cache_enabled = False` bypasses everything.

---

## 10. Open decisions

1. **Agree to defer the cache** and adopt the §1.3 triggers, or build it anyway
   for the expected growth.
2. **When it is built: a separate `kuis-cache` or the shared `kuis-redis`** with
   `volatile-lru`. This plan recommends separate (§6.6).
3. **Legacy KWIC retirement**, the other half of Phase 8. Per
   `docs/data-processing.md` §9 it needs a product confirmation, independent of
   anything here.
