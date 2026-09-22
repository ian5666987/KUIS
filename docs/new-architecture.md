# KUIS Architecture Revamp — Plan & Status

> Living tracking doc for the Django → Next.js + Django + FastAPI + Worker
> revamp. The design below was produced in a planning session on
> 2026-09-22 and approved as-is; this file's **Status** section is updated
> as work lands. Companion frontend repo: `KUIS-FE`, sibling to this repo.

---

## Status

**Legend:** ✅ done and verified · 🚧 in progress · ⬜ not started

| Phase | What it covers | Status |
|---|---|---|
| 0 | Repo scaffolding, Docker, CI — zero behavior change | ✅ done (2026-09-22) |
| 1 | JWT auth (Django SimpleJWT + FastAPI verification + KUIS-FE login) | ✅ done (2026-09-22) |
| 2 | FastAPI skeleton + all 5 repository ABCs + fast-KWIC port | ✅ done (2026-09-22) |
| 3 | Next.js MVP consuming fast-KWIC (first true end-to-end proof) | ⬜ not started |
| 4 | Remaining feature ports: word frequency → collocations → n-grams | ⬜ not started |
| 5 | Tier 1/2 schema optimization (`WordType`, `DocumentWordFreq`, `DocumentNgram`) | ⬜ not started |
| 6 | Error Analytics (new build, blocked on error-taxonomy doc) | ⬜ not started |
| 7 | Caching (Tier 4) + retire superseded Django views | ⬜ not started |

### Phase 0 — done, what actually landed

**KUIS-FE** (new repo, `/Users/supernovadesilentio/Documents/indoleksia/KUIS-FE`, uncommitted):
- Next.js 16 App Router + TypeScript scaffold, builds and lints clean.
- Route structure: `(auth)/login`, `(dashboard)/{dashboard,corpus,corpus/[id],analysis/{kwic,frequency,collocations,ngrams,error-analytics}}`.
- `app/api/auth/{login,refresh,logout}/route.ts` — proxy only the auth endpoints, set httpOnly cookies. Calls Django's not-yet-existing `/api/auth/token/*` (Phase 1).
- `proxy.ts` route guard (Next.js 16 renamed `middleware.ts` → `proxy.ts` — used the new convention from the start).
- `lib/api/{config,token,django-client,fastapi-client}.ts` + `lib/api/types/{django,fastapi}.d.ts` placeholders, ready to swap in `openapi-typescript`-generated types once each backend has real endpoints.
- The httpOnly-cookie open design question raised in Phase 0 is **resolved in Phase 1** — see below.

**KUIS backend** (this repo):
- `config/settings.py`: `ALLOWED_HOSTS` is now env-driven (was hardcoded `[]`, which rejects everything once `DEBUG=False`); `STATIC_ROOT` added for `collectstatic`.
- `dataplane/` — FastAPI app skeleton, `/health` only (Phase 1 added `/api/v1/whoami`, still not a real feature — see below). Verified working both via `uvicorn` locally and inside the built Docker image.
- `worker/` — Celery app skeleton (`worker/celery_app.py`), zero tasks registered. Verified it boots against Django's settings via `django.setup()`.
- `docker/{django,fastapi,worker}.Dockerfile`, `docker-compose.yml` (local dev; `app-shared` network required — see file comment), `.dockerignore`.
- `.github/workflows/{ci,deploy}.yml` — CI runs Django's test suite + import-checks the FastAPI/Celery apps; deploy mirrors `identity-service-internal`'s real pattern, built as a 3-image matrix.
- `requirements.txt` / `.env.example` updated for the new deps (`fastapi`, `uvicorn`, `pydantic-settings`, `celery`, `redis`) and the new `REDIS_URL` var.

**Verified, not just written:**
- Django's 29 existing tests pass unchanged (`DB_ENGINE=django.db.backends.sqlite3 DB_NAME=:memory: python manage.py test main`).
- `dataplane`'s `/health` responds `{"status": "ok"}` locally and from the built `docker/fastapi.Dockerfile` image.
- `worker/celery_app.py` imports and constructs cleanly against real Django settings.
- Next.js scaffold: `npm run build` and `npm run lint` both pass clean.

**Known issue to fix before relying on `docker-compose.yml` in production:** the real `.env`'s `SECRET_KEY` contains a `$` character, which Compose's variable interpolation partially swallows. Escape it as `$$`, or regenerate `SECRET_KEY` without special characters.

**Not done from Phase 0's scope:** nothing is committed to git in either repo (by design — commits weren't requested). The `/opt/platform` server-side compose file and nginx routing entries for the three new KUIS images live outside any repo and haven't been touched.

### Phase 1 — done, what actually landed

**KUIS backend:**
- `rest_framework`, `rest_framework_simplejwt`, `rest_framework_simplejwt.token_blacklist` added to `INSTALLED_APPS`; `SIMPLE_JWT` config added (`config/settings.py`) with a 15-minute access token, 7-day refresh token, rotation + blacklist-on-rotation enabled, signed with a new `JWT_SECRET` env var kept deliberately separate from `SECRET_KEY`.
- `main/api_auth.py` — `KUISTokenObtainPairSerializer`/`View` adding `is_staff`, `username`, `email` claims on top of SimpleJWT's default `user_id`. Confirmed `main.auth_backends.UsernameOrEmailBackend` needed zero changes — `authenticate()` walks the same `AUTHENTICATION_BACKENDS` list regardless of caller.
- Three new additive routes in `config/urls.py`: `POST /api/auth/token/`, `/token/refresh/`, `/token/blacklist/`. The existing session-cookie `accounts/login/`/`accounts/logout/` routes are untouched.
- `main/tests.py::JWTAuthTests` (6 new tests) — obtain/wrong-password, username-or-email login, claim shape, refresh rotation + old-token-reuse rejection, blacklist-then-refresh rejection, and an explicit check that session-cookie login still works. Full suite: **35/35 passing** (29 original + 6 new).

**KUIS backend, FastAPI side:**
- `dataplane/core/security.py` — stateless HS256 verification against the shared `JWT_SECRET`; fails loudly (500) if misconfigured rather than silently rejecting every token as invalid (401).
- `dataplane/dependencies.py` — `get_current_user` / `require_staff`, the FastAPI-side re-expression of `main/views.py::staff_required`'s exact is-staff-only rule.
- `dataplane/routers/whoami.py` — new `GET /api/v1/whoami`, protected, added specifically as this phase's end-to-end proof; `/health` deliberately stays open (container health checks shouldn't need a token).
- `dataplane/tests/` (15 new tests): `test_security.py` (valid/expired/wrong-key/wrong-token-type/missing-claims/missing-secret, all as isolated unit tests with hand-crafted tokens — no Django dependency), `test_whoami.py` (real endpoint behavior via FastAPI's `TestClient`), `test_jwt_consistency.py` (the one test allowed to import Django — confirms Django's `SIGNING_KEY` and FastAPI's `JWT_SECRET` actually resolve to the same value, catching secret drift between the two services).

**KUIS-FE:**
- Resolved the Phase 0 open question: the access-token cookie is now set `httpOnly: false` on purpose. Cookies aren't sent cross-origin automatically regardless of httpOnly, so a Client Component needs to read the token itself to attach it as a bearer header on direct Django/FastAPI calls — httpOnly was blocking that calling pattern for no real security gain given the 15-minute lifetime. The refresh token stays httpOnly, scoped to `/api/auth`.
- `lib/api/refresh-on-401.ts` — new single-flight refresh-and-retry helper, wired into both `django-client.ts` and `fastapi-client.ts`, so an expired access token mid-session gets silently refreshed and the request retried once instead of surfacing a 401.
- **Two real bugs found and fixed by testing against a live Django instance, not just reading the code:**
  1. `refresh/route.ts` only read/saved the `access` field from Django's refresh response. Django's `ROTATE_REFRESH_TOKENS=True` means that response also contains a **new** `refresh` token, and the old one is immediately blacklisted — the stub would have broken every session's *second* refresh. Fixed to save both.
  2. `logout/route.ts` (and `refresh/route.ts`'s error branch) called `.delete(name)` on the refresh-token cookie without its original `path: "/api/auth"`. Cookie deletion in Next.js (and browsers generally) only overrides a cookie if the path matches what it was set with — the refresh token was silently surviving logout. Confirmed with a real cookie jar before and after the fix. Fixed by passing the matching path to `.delete()`.

**Verified, not just written (this phase):**
- Full JWT lifecycle against a live Django instance over real HTTP: obtain via username, obtain via email, wrong password rejected (401), refresh rotation returns both new tokens, reusing the old refresh token fails (401), blacklist-then-refresh fails (401).
- A JWT minted by that live Django instance was decoded and accepted by a live FastAPI instance with **zero** calls back to Django or any database — confirmed by inspecting the FastAPI process's own log output.
- Existing session-cookie login (`accounts/login/` → `/dashboard/`) still works unmodified on the same running Django instance.
- The real Next.js dev server, talking to the real Django instance through its own `/api/auth/*` Route Handlers (not mocked), correctly sets a readable access-token cookie and an httpOnly, path-scoped refresh-token cookie, and correctly clears both on logout after the path-mismatch fix.
- `main/tests.py`: 35/35. `dataplane/tests/`: 15/15. KUIS-FE `npm run build` and `npm run lint`: clean.

**Not done from Phase 1's scope:** nothing is committed to git in either repo. No Django DRF endpoint exists yet for anything beyond auth (document status, corpus metadata, etc. — those are Phase 5+). No load-bearing feature calls any of this yet; `analysis/kwic` and friends are still stubs (Phase 2/3/4).

### Phase 2 — done, what actually landed

**The fast-KWIC port is real and parity-checked against live Django output**, not just written and unit-tested in isolation. All 5 repository ABCs are defined; `KWICRepository` is the one with a working Postgres implementation.

- `dataplane/repositories/base.py` — all 5 ABCs (`KWICRepository`, `FrequencyRepository`, `CollocationRepository`, `NgramRepository`, `ErrorAnalyticsRepository`) plus shared `Mode`/`MatchMode`/`SortDirection` enums and domain dataclasses. Only `KWICRepository`'s shape has been proven against real data; the other four are explicitly marked provisional in their own docstrings.
- `dataplane/models/tables.py` — SQLAlchemy Core tables for `main_document`, `main_corpus`, `main_token`, `main_corpus_documents`, with every table/column name confirmed against Django's actual `_meta` (not guessed).
- `dataplane/core/db.py` — async engine/session factory, `get_db_session` FastAPI dependency.
- `dataplane/repositories/shared.py` — `resolve_document_ids(session, corpus_ids)`, the corpus→document resolution every future feature repository will also need (mirrors `main/views.py::_get_selected_documents`'s `.distinct()` dedup).
- `dataplane/repositories/postgres/kwic_repository.py` — ports `_word_index_matches`/`_hydrate_word_index` onto an N-word self-join over `Token`, **generalized from single-word to phrase search** so this one repository absorbs legacy `kwic` too (architecture plan §4) — confirmed below, not just asserted. Also fixes `optimisation_plan.md` defects #3 (materializing every match before paginating) and #4 (loading a whole document to slice a window): pagination is a real SQL `LIMIT`/`OFFSET`, total count is a separate `COUNT(*)`, and context windows for an entire page of hits are fetched in one batched query via `position BETWEEN`.
- `dataplane/services/kwic_service.py`, `dataplane/schemas/kwic.py`, `dataplane/routers/kwic.py` — new `GET /api/v1/kwic`, wired into `dataplane/main.py`. Any authenticated user may call it (matches `kwic_search`'s plain `@login_required`, no staff requirement).
- `dataplane/tests/test_schema_drift.py` — new CI guard comparing `dataplane/models/tables.py` against Django's real `_meta` column-for-column; deliberately broken and confirmed to fail before being confirmed to pass clean.
- `pytest.ini` (new) + `pytest-asyncio` — needed once dataplane tests stopped being pure-Python unit tests and started touching an async database.

**One real bug found by testing against actual Postgres, not caught by writing or reading the code:** the first cut of the self-join query built its match-finding `SELECT` from an aliased `Token` (`t0`, `t1`, ...) but then tried to `ORDER BY` the *unaliased* `main_token` table — a query Postgres rejects at execution time (`invalid reference to FROM-clause entry for table "main_token"`), invisible from reading the SQLAlchemy Python code alone. Fixed by threading the base alias through to the caller instead of reaching back for the module-level `token` table object.

**A second issue, test-infrastructure rather than product code:** `TestClient` + a module-level pooled async engine don't mix across multiple test functions — each `TestClient` spins its own event loop, and a pooled connection checked out in a since-closed loop crashes on its next use (`RuntimeError: Event loop is closed`). Fixed by giving router-level tests their own `NullPool`-based engine via `app.dependency_overrides`, scoped to the test client only; the production engine's real connection pooling is untouched.

**Verified, not just written (this phase):**
- A dedicated local test database (`kuis_dataplane_test`, migrated via Django, seeded with known fixture data — the same `<segment Correction=...>` XML shape `main/tests.py` already uses) was created specifically so none of this testing ever touched the real dev database (`corpus_db`). Confirmed before and after: still exactly 3 documents / 2 corpora / 1090 tokens, same titles and ids, completely untouched.
- **Byte-for-byte parity**, live, against the real Django app running on that same seeded database: single-word search against `/analysis/kwic/search/export/` CSV, two-word phrase search against legacy `/analysis/kwic/export/` CSV, and corrected-mode search (`tetapi` via a segment's `Correction` attribute) — all three matched the new `/api/v1/kwic` endpoint's output exactly, including document-boundary window truncation.
- Auth enforcement end-to-end: a request with no bearer token gets 401; a Django-issued token works; missing required query params (`q`, `corpus_ids`) get 422; an unknown `corpus_id` returns an empty result set, not an error.
- The schema-drift guard was deliberately broken (renamed a column in `tables.py`) and confirmed to fail with a clear message, then confirmed to pass again once reverted.
- 37/37 `dataplane/tests/` passing (11 repository, 7 router, 4 schema-drift, plus the 15 from Phase 1), run against the real Postgres test database exactly as the updated CI job now does. `main/tests.py`: still 35/35, untouched by this phase.
- `.github/workflows/ci.yml`'s `dataplane-tests` job now runs a real `postgres:16` service container (previously sqlite-only, which can't run these tests at all — asyncpg has no sqlite driver) and runs Django's migrate against it before the dataplane suite, matching how the real deployment bootstraps.

**Not done from Phase 2's scope:** `analysis/kwic` in KUIS-FE is still a stub — wiring a real page to this endpoint is Phase 3. `FrequencyRepository`/`CollocationRepository`/`NgramRepository`/`ErrorAnalyticsRepository` have interfaces but zero implementations. `dataplane/dependencies.py` has no `get_frequency_service` etc. yet. Nothing is committed to git in either repo.

---

## Context

KUIS is currently a pure Django 6.0.4 server-rendered monolith (`config/` + one app `main/`) with no REST API, no JS framework, no background worker, and no Docker/CI. `docs/ONBOARDING.md` documents it as-is; `docs/optimisation_plan.md` (already in-repo) separately diagnoses real scaling problems in how it stores and queries corpus tokens — four of its five analysis features re-parse raw document XML on every request with no cache, and the one feature that does query a precomputed `Token` table (fast-KWIC) still does it inefficiently.

The target architecture splits this into:

```
Next.js (new repo, KUIS-FE)
        |
   +----+----+
Django    FastAPI
Back      Data Plane
Office    (KWIC, Frequency, N-gram, Collocation, Error Analytics)
   |         |
   +----+----+
        |
   PostgreSQL
        |
      Worker (preprocessing, tokenization, statistics)
```

with an explicit design goal: FastAPI endpoints must never depend directly on Postgres-specific query logic, so that a `FrequencyRepository`/`KWICRepository` can later be swapped for ClickHouse/OpenSearch without touching the Service layer, the endpoint layer, or the Next.js frontend.

Three decisions were confirmed before designing further:
1. **Auth**: Django issues JWTs (source of truth for identity/RBAC); FastAPI verifies them statelessly.
2. **Migration path**: incremental strangler-fig — Django keeps serving every current page throughout; nothing is deleted until its replacement ships.
3. **Repo layout**: one backend repo (this repo) hosts Django, FastAPI, and the worker as three deployable processes sharing one schema-migration history; a new sibling repo `KUIS-FE` holds the Next.js frontend.

Org-wide research found no existing Python/FastAPI or Next.js precedent anywhere else in the org (every other service is Node/TypeScript + React Router v7 SSR on a proprietary codegen framework) — this revamp establishes both patterns fresh for KUIS specifically, while reusing the org's actual deployment shape: one Ubuntu VPS, per-repo docker-compose stacks, a single nginx gateway routing by hostname to internal-only container ports, GHCR image push + SSH deploy on `workflow_dispatch`.

`docs/optimisation_plan.md`'s tiers are treated as the concrete spec for what the FastAPI repository layer needs to eventually implement (Tier 0: query the `Token` table instead of re-parsing; Tier 1: normalize vocabulary into a `WordType` table; Tier 2: precompute per-document aggregates; Tier 3: move tokenization off the request path; Tier 4: cache). This plan interleaves those tiers with the feature-by-feature UI migration rather than doing either in one big-bang pass.

---

## 1. KUIS repo restructuring

Same repo, three processes, one dependency file, one schema owner (Django's `main/migrations/` — FastAPI and the worker are schema *consumers*, never issue their own migrations):

```
KUIS/
  manage.py
  requirements.txt              # Django + FastAPI + Celery deps together
  config/                       # unchanged
  main/                         # unchanged initially — every current view/template stays live
  dataplane/                    # FastAPI data-plane app
    main.py                     # FastAPI() instance, router includes, lifespan (DB pool)
    dependencies.py             # get_db_session, get_current_user, require_staff, get_repository(...)
    core/{config.py, security.py, db.py}   # settings, JWT verify, SQLAlchemy async engine
    models/tables.py            # SQLAlchemy Table defs mirroring Django's tables column-for-column
    schemas/{frequency,collocation,ngram,kwic,error_analytics,common}.py
    repositories/
      base.py                   # ABCs: FrequencyRepository, CollocationRepository, NgramRepository, KWICRepository, ErrorAnalyticsRepository
      postgres/{frequency,collocation,ngram,kwic,error_analytics}_repository.py
      # future: repositories/clickhouse/…, repositories/opensearch/kwic_repository.py — same ABCs, drop-in
    services/{frequency,collocation,ngram,kwic,error_analytics}_service.py
    routers/{frequency,collocation,ngram,kwic,error_analytics,health}.py
  worker/                       # Celery
    celery_app.py               # django.setup() first, then imports main.models directly
    tasks/{indexing,aggregates,maintenance}.py
  docker/{django,fastapi,worker}.Dockerfile
  docker-compose.yml
  .github/workflows/{ci,deploy}.yml
```

*(Status: the tree above exists, including `core/db.py`, `models/tables.py`, and the KWIC slice of `repositories/`, `repositories/postgres/`, `services/`, `schemas/`, `routers/` — all written and parity-tested in Phase 2. `repositories/base.py` defines all 5 ABCs; only `KWICRepository` has a concrete implementation. Frequency/Collocation/Ngram/ErrorAnalytics have no `services/`/`schemas/`/`routers/` files yet — Phase 4/6. `worker/tasks/*` is still empty — Phase 5.)*

**What maps to what** (reuse, don't duplicate):

| Current code | Becomes |
|---|---|
| `main/models.py::build_tokens()` + its `post_save` signal | Called explicitly (not via signal) by `worker/tasks/indexing.py::index_document()` |
| `main/views.py::_count_words(size=1)` + `_rank_counter` ([views.py:589-639](../main/views.py#L589-L639)) | `dataplane/services/frequency_service.py` + `postgres/frequency_repository.py` |
| `_count_words(size=2)` ([views.py:661-693](../main/views.py#L661-L693)) | `collocation_repository.py` (internally may delegate to the n-gram query builder with `n=2`, but keeps its own public interface — collocations have their own product future, e.g. mutual-information scoring) |
| `_count_words(size=n)` ([views.py:696-750](../main/views.py#L696-L750)) | `ngram_repository.py` |
| `_word_index_matches` / `_hydrate_word_index` ([views.py:934-980](../main/views.py#L934-L980)) | `kwic_repository.py` — ported *and* fixed (see §4) |
| `main/corpus_parsing.py::parse_document` | Stays the one tokenizer, imported by the worker; extended (not duplicated) for error-analytics in Phase 6 |
| `staff_required` ([views.py:51-59](../main/views.py#L51-L59)) | `dataplane/dependencies.py::require_staff` — same rule, re-expressed as a FastAPI dependency |

**Repository interfaces**: define all 5 ABCs in `dataplane/repositories/base.py` up front, before every concrete implementation exists, so later ports slot into an already-agreed shape. Use `abc.ABC` (load-time-enforced contract, not just a type-checker convention). Example, ported from `_word_index_matches`/`_hydrate_word_index`:

```python
class KWICRepository(ABC):
    @abstractmethod
    async def search(self, document_ids: list[int], words: list[str], mode: Mode,
                      window: int, limit: int, offset: int) -> KWICPage: ...
    @abstractmethod
    async def count_matches(self, document_ids: list[int], words: list[str], mode: Mode) -> int: ...
```

`words: list[str]` (a phrase, not a single word) is deliberate — it's what lets this one interface absorb legacy KWIC too (§4).

---

## 2. Database / schema plan

Django's migrations stay the only schema authority. FastAPI's `dataplane/models/tables.py` hand-mirrors the same tables via SQLAlchemy; the worker writes through Django's ORM, never a parallel path.

**New Tier-1 migrations** (after `0006_legacy_corpus`): `WordType(id, form varchar(100) UNIQUE)`; backfill; `Token.word_type` FK replacing `Token.word`; `Token.mode` → smallint. Treat dropping `Token`'s bigint surrogate PK for a composite `(document_id, mode, position)` PK, and adding a `BrinIndex` on `document_id`, as optional stretch items within this same migration batch — cheap if included now, not required to unblock anything else. Add `Document.content_hash` / `tokenized_hash` / `tokenizer_version` as nullable placeholder columns in this same batch (per `optimisation_plan.md` §5's secondary integrity-tracking note) — reserving them now avoids a second backfill pass later.

**New Tier-2 migrations**: `DocumentWordFreq(document, word_type, mode, count)` and `Ngram`/`DocumentNgram` (top-K per document with a frequency floor, not every n-gram — the worker task decides K and the floor, not the schema).

**Who writes**: the worker, via Celery tasks calling `main.models` directly — `index_document()` wraps `build_tokens()` (extended to resolve `word_type_id`) in `transaction.atomic()` with `bulk_create(batch_size=5000)`, fixing `optimisation_plan.md` defects #1/#2 (unbounded bulk_create, no transaction). `compute_document_word_freq`/`compute_document_ngrams` aggregate from `Token` and upsert via `bulk_create(update_conflicts=True, ...)`.

**Who reads**: FastAPI, via SQLAlchemy against the same tables. Because two independent schema definitions now exist (Django's ORM, FastAPI's SQLAlchemy `Table` objects), add a CI check: a Django management command dumps `(table, column, type)` from `main.models._meta`, diffed against `dataplane/models/tables.py`'s metadata — a concrete guard against silent drift, which is the main risk of the consumer/owner split.

*(Status: none of this exists yet — Phase 5.)*

---

## 3. Auth implementation

`djangorestframework` + `djangorestframework-simplejwt` (+ `token_blacklist`), not hand-rolled PyJWT — `TokenObtainPairView` calls Django's standard `authenticate()`, which already walks `AUTHENTICATION_BACKENDS` ([config/settings.py:132-135](../config/settings.py#L132-L135)), so `UsernameOrEmailBackend` ([main/auth_backends.py](../main/auth_backends.py)) works with **zero changes** — login-by-username-or-email is inherited for free.

```python
# config/settings.py additions
INSTALLED_APPS += ['rest_framework', 'rest_framework_simplejwt', 'rest_framework_simplejwt.token_blacklist']
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=15),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'SIGNING_KEY': env('JWT_SECRET'),   # separate from SECRET_KEY, new .env key
}
```

Custom claims serializer adds `is_staff`, `username`, `email` to the token (alongside SimpleJWT's default `user_id`). New additive endpoints only — `POST /api/auth/token/`, `/token/refresh/`, `/token/blacklist/` — the existing session-cookie `login`/`logout` routes in `config/urls.py` are untouched and keep serving the server-rendered pages throughout the whole migration.

FastAPI verifies statelessly: `dataplane/core/security.py` decodes with the shared `JWT_SECRET` (same `.env`, same deploy, costs nothing extra since both live in one repo/compose stack), no DB or Django call per request. `dataplane/dependencies.py::require_staff` re-expresses `staff_required`'s exact rule. Next.js decodes claims client-side purely for UI gating (hide upload/corpus-create controls) — cosmetic only, exactly like today's `{% if user.is_staff %}`; the real boundary stays server-side in both Django and FastAPI.

*(Status: ✅ done and verified end-to-end — see the Phase 1 status block above.)*

---

## 4. Feature-by-feature migration sequence

Order: **fast-KWIC → word frequency → collocations → n-grams → legacy KWIC (folded in, not separately ported)**. "Done" per step = FastAPI endpoint exists and is parity-checked against the live Django view, Next.js page consumes it, the old Django view stays reachable and untouched until Phase 7.

**Fast-KWIC first**: `kwic_search` ([views.py:984-1016](../main/views.py#L984-L1016)) is the only feature already reading `Token` instead of re-parsing content — porting it is pure plumbing (translate an existing ORM query to SQLAlchemy), validating JWT auth, the Repository/Service/Router layering, and the Next.js data-fetching pattern all at once, in the lowest-risk feature. **✅ Done in Phase 2, parity-confirmed against live Django output** — see the Phase 2 status block above. Still open: the Next.js data-fetching pattern isn't proven yet (Phase 3) — this phase only proved the FastAPI side.

**Word frequency next**: becomes `SELECT word, COUNT(*) ... GROUP BY word ... LIMIT/OFFSET`. Ship this against the *raw* `word` column (Tier-0 shape) — it doesn't need to wait for the Tier-1 `WordType` migration. This is the key sequencing insight: **feature ports and schema-tier work are orthogonal**, because the Repository boundary is exactly what makes re-optimizing a query later invisible to everything above it.

**Collocations / n-grams**: need a window function (`LAG`/`LEAD` over `(document_id, mode)` ordered by `position`) — ship a "correct but not yet fast" version against raw `Token` first, then swap the repository's internals onto `DocumentNgram` once Tier 2 lands (Phase 5) — same Service, Router, and Next.js page, only the repository's SQL changes. This is the repository-swap payoff happening once *before* ClickHouse ever enters the picture.

**KWIC repository fixes known defects while porting**: current `_word_index_matches` ([views.py:942-947](../main/views.py#L942-L947)) materializes every match before paginating (defect #3); `_hydrate_word_index` ([views.py:960-967](../main/views.py#L960-L967)) loads a document's whole word list to slice a ±N window (defect #4). The new `KWICRepository.search()` instead does a separate `COUNT(*)`, a real `LIMIT/OFFSET` (or keyset) for the page, and `position BETWEEN :lo AND :hi` per hit for context. **✅ Done** — see `dataplane/repositories/postgres/kwic_repository.py`.

**Legacy KWIC's redundancy, resolved**: `kwic` (phrase search, live-parsed) and `kwic_search` (single-word, Token-table) differ only in phrase-vs-word and live-parse-vs-indexed. Extending `KWICRepository.search()` to accept `words: list[str]` (already in the §1 interface) makes the fast path a strict superset — a multi-word query is a handful of position-shifted joins (query length is capped at `KWIC_WINDOW_MAX`=10). **No separate port of legacy `kwic` is planned**; one repository/service/router/Next.js page covers both once phrase support ships. `kwic` stays alive in Django until Next.js's concordance page is confirmed to cover it, then retires in Phase 7. **✅ Phrase support is done and parity-checked against live `kwic` output** (not just `kwic_search`'s single-word case) — the redundancy claim is now proven, not just argued. Still worth a quick product confirm before actually retiring the Django view in Phase 7.

*(Status: **fast-KWIC ✅ done** (Phase 2). Word frequency, collocations, n-grams still not started — Phase 4.)*

---

## 5. Worker design

Celery + Redis (new `kuis-redis` service, doubling later as the Phase-7 cache backend — no task-queue precedent exists elsewhere in the org, Celery is the standard mature choice and its periodic-task support suits future scheduled stats jobs).

- `worker/tasks/indexing.py::index_document(document_id)` — the async replacement for the `post_save` signal ([models.py:87-96](../main/models.py#L87-L96)). Triggered explicitly via `index_document.delay(doc.id)` from `corpus_create`/`corpus_edit`/`upload_document` right after `doc.save()`, **instead of** the signal, which is disabled once this ships — removing the synchronous signal is the actual point of Tier 3.
- `worker/tasks/aggregates.py::compute_document_word_freq/compute_document_ngrams(document_id)` — chained after indexing so `Document.status` only flips to `ready` once tokens *and* aggregates exist.
- `worker/tasks/maintenance.py::retokenize_document/retokenize_all` — Celery equivalent of `main/management/commands/retokenize.py`, fixing defect #7 (no `.iterator()`) by chunking and dispatching one task per document instead of loading everything into one process.

New `Document.status` field (`indexing`/`ready`/`failed`) shown as a badge in `corpus_detail.html`'s existing per-document rows and in `main/admin.py`, plus a small DRF endpoint (`GET /api/documents/<id>/status/`) Next.js polls after upload.

*(Status: `worker/celery_app.py` exists and boots with zero tasks. `worker/tasks/{indexing,aggregates,maintenance}.py` are unwritten — Phase 5.)*

---

## 6. KUIS-FE repo scaffolding

New sibling repo: `/Users/supernovadesilentio/Documents/indoleksia/KUIS-FE`. Next.js App Router + TypeScript (no org convention forces Pages router; App Router is the modern default).

```
KUIS-FE/
  app/
    (auth)/login/page.tsx
    (dashboard)/
      dashboard/page.tsx
      corpus/page.tsx  corpus/[id]/page.tsx
      analysis/{page.tsx, kwic/, frequency/, collocations/, ngrams/, error-analytics/}
    api/auth/{login,refresh,logout}/route.ts   # thin proxy ONLY for auth (httpOnly cookies)
  lib/api/{django-client,fastapi-client}.ts
  lib/api/types/{django.d.ts,fastapi.d.ts}      # generated, not hand-written
  proxy.ts                                       # route guard + is_staff UI gating (cosmetic)
```

**Typed clients**: the org's `@naiv/codegen-axios-client` tool is wired to the Node backends and doesn't apply here. Use `openapi-typescript` to generate types straight from each backend's OpenAPI doc — FastAPI emits `/openapi.json` natively; Django gets `drf-spectacular` (alongside DRF/SimpleJWT) for its handful of Next.js-facing endpoints (auth, document status). Pair with `openapi-fetch` (same author) for a small typed fetch wrapper instead of pulling in axios and reinventing interceptor conventions that don't transfer from the Node side.

**Calling pattern**: Next.js Route Handlers proxy only the auth endpoints (to set the refresh cookie httpOnly without exposing it to client JS). Every other data call goes directly from the browser/Server Components to Django or FastAPI with the bearer token attached — matching the target diagram where Next.js talks to both backends directly. **Resolved in Phase 1** — see the Phase 1 status block above for why the access-token cookie ended up non-httpOnly.

**Corpus selection state**: Django currently keeps this server-side in the session ([views.py:382-402](../main/views.py#L382-L402)), which doesn't translate to a stateless JWT client. Continue the shareable-URL pattern KUIS already uses (`docs/ONBOARDING.md` §7 — "Ids also travel in the query string... so feature URLs stay shareable"): carry selected corpus ids as Next.js URL search params, just without the session fallback.

*(Status: scaffold exists and builds/lints clean. All pages are stubs with no real data — see the KUIS-FE repo's own README for the per-page dependency table.)*

---

## 7. Deployment additions

Matches the verified house pattern (checked against `identity-service-internal`'s real Dockerfile/`deploy.yml` and `identity-admin-web`'s real FE Dockerfile): GHCR push, `linux/amd64`, `workflow_dispatch`-gated deploy, SSH + `docker compose pull && up -d`, internal-only container ports (no host-published ports), one nginx gateway as the sole public entry.

**`KUIS/docker/`**: `django.Dockerfile` (`python:3.14-slim`, `gunicorn config.wsgi:application --bind 0.0.0.0:9415`); `fastapi.Dockerfile` (`uvicorn dataplane.main:app --host 0.0.0.0 --port 9415`, same port number as Django's container but a separate container resolved by name on the shared network); `worker.Dockerfile` (`celery -A worker.celery_app worker`, no exposed port).

**`KUIS/docker-compose.yml`**: `kuis-postgres`, `kuis-redis`, `kuis-django`, `kuis-fastapi`, `kuis-worker`, all on an external `app-shared` network, no `ports:` on any app service — nginx reaches each by container name, exactly as the house pattern specifies.

**CI/CD**: `.github/workflows/ci.yml` (push to any branch) and `deploy.yml` (push to `main` + `workflow_dispatch`, build matrix over `[django, fastapi, worker]` → three GHCR images from one repo, deploy job gated on `workflow_dispatch` only) — same shape as `identity-service-internal/.github/workflows/deploy.yml`, generalized to three images.

**KUIS-FE**: its own Dockerfile/compose/CI, as a second independent stack. *(Status: not yet added to KUIS-FE — Phase 0 follow-up.)*

*(Status: all three Dockerfiles, `docker-compose.yml`, `.dockerignore`, and both workflow files exist in this repo and have been build/run-verified — see the top status table. The `/opt/platform` server-side compose entries and nginx routes are NOT done — that's outside any repo, on the VPS itself. KUIS-FE's own Docker/CI files are also not done yet.)*

---

## 8. Phased execution order

1. **Repo scaffolding, zero behavior change** — ✅ done. Fixed `ALLOWED_HOSTS`/`STATIC_ROOT`, added all three Dockerfiles + compose + CI for KUIS, scaffolded empty `dataplane/` (`/health` only) and `worker/` (no tasks yet), created the `KUIS-FE` skeleton. KUIS-FE's own Docker/CI files are the one Phase-0 item still outstanding. Django still serves 100% of traffic.
2. **JWT auth** — ✅ done. DRF + SimpleJWT + blacklist, custom claims, FastAPI verification proven against a new protected `/api/v1/whoami` endpoint (not `/health`, which stays deliberately open), KUIS-FE login page + cookie proxy + refresh-on-401 retry, all verified against a live Django + FastAPI + Next.js dev server chain. `proxy.ts`'s guard still only checks cookie *presence*, by design — see its comment for why deeper verification isn't needed there. No analysis features exposed yet (that starts Phase 2/3).
3. **FastAPI skeleton + fast-KWIC port** — ✅ done. All 5 repository ABCs defined; `KWICRepository` has a real Postgres implementation + service + router, parity-checked against BOTH `/analysis/kwic/search/` (single-word) and `/analysis/kwic/` (phrase search) — byte-for-byte matching output on a real Postgres database.
4. **Next.js MVP on fast-KWIC** — ⬜ not started. Corpus picker + concordance results, the first true end-to-end proof of the whole target architecture.
5. **Remaining feature ports** — ⬜ not started. Word frequency, then collocations, then n-grams (window-function version against raw `Token`); legacy KWIC folded into fast-KWIC's phrase-search extension, not separately ported.
6. **Tier 1/2 schema optimization** — ⬜ not started. `WordType`, normalized `Token`, `DocumentWordFreq`, `DocumentNgram` migrations; worker starts populating them plus a backfill for existing documents; frequency/collocation/ngram repositories swap their internals onto the new tables with Service/Router/Next.js untouched.
7. **Error Analytics (new build)** — ⬜ not started, and blocked on a documented error-type taxonomy (product dependency, not engineering — flagged in `docs/ONBOARDING.md` §3/§9).
8. **Caching (Tier 4) + cleanup** — ⬜ not started. Redis-backed cache at the Service layer, keyed on `(sorted corpus ids, mode, feature, n)`. This is also when the now-superseded Django live-parse views finally retire.

---

## Verification

- **Per-feature parity**: for each FastAPI port, run a script comparing its JSON output against the equivalent Django view's rendered/CSV-export data for a fixed corpus selection, before wiring the Next.js page to it. ✅ done for fast-KWIC — compared live against both `/analysis/kwic/search/export/` and `/analysis/kwic/export/` CSV output on a real Postgres database seeded with known data; exact match on every row.
- **Auth**: confirm a JWT obtained from `POST /api/auth/token/` is accepted by a FastAPI endpoint with no Django call in the request path; confirm the existing session-cookie login still works unmodified. ✅ done — verified manually end-to-end and now covered permanently by `main/tests.py::JWTAuthTests` + `dataplane/tests/`.
- **Schema drift check**: CI command comparing Django's `_meta` schema dump against `dataplane/models/tables.py`. ✅ done in Phase 2, ahead of the original Phase 5 schedule — `dataplane/tests/test_schema_drift.py`, part of the CI `dataplane-tests` job. Deliberately broken (a column renamed) and confirmed to fail with a clear message, then confirmed to pass again once reverted. Will need extending, not rewriting, once Tier 1/2 tables are added in Phase 5.
- **Django regression**: `DB_ENGINE=django.db.backends.sqlite3 DB_NAME=:memory: python manage.py test main` must keep passing unmodified throughout every phase. ✅ passing as of Phase 0 (29/29).
- **Worker**: upload a document, confirm `Document.status` moves `indexing` → `ready`. *(Not yet applicable — Phase 5.)*
- **Deployment**: `docker compose build` succeeds for all three KUIS services plus KUIS-FE; each container starts and responds on its internal port. ✅ verified for `dataplane` (built image + container run + `/health` response). `django`/`worker` images not yet build-tested; KUIS-FE has no Dockerfile yet.
- **End-to-end**: from KUIS-FE, log in, select a corpus, run KWIC — confirm results match the legacy Django page. *(Not yet applicable — Phase 3.)*
