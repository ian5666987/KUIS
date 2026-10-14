# KUIS — Changelog

> Condensed history of the Django → Next.js + Django + FastAPI + Worker revamp,
> planned 2026-09-22 and executed in phases. This is the record of *when* things
> landed; what they are is `docs/feature-list.md`, how they work is
> `docs/data-processing.md`. Full per-phase detail lives in git history.

## Where it started

A pure Django 6.0.4 server-rendered monolith (`config/` + one app `main/`) with
no REST API, no JS framework, no background worker, and no Docker/CI. Four of its
five analysis features re-parsed raw document XML on every request with no cache;
the one feature that queried a precomputed `Token` table did it inefficiently.

Three decisions were confirmed before any code moved:

1. **Auth** — Django issues JWTs (the source of truth for identity and RBAC);
   FastAPI verifies them statelessly.
2. **Migration path** — incremental **strangler fig**: Django keeps serving every
   current page throughout, and nothing is deleted until its replacement ships.
   This is why both surfaces are still live today.
3. **Repo layout** — one backend repo hosting Django, FastAPI and the worker as
   three deployable processes sharing one migration history, plus a new sibling
   repo `KUIS-FE` for Next.js.

No Python/FastAPI or Next.js precedent existed anywhere else in the org (every
other service is Node/TypeScript + React Router v7 SSR on a proprietary codegen
framework), so this established both patterns fresh while reusing the org's real
deployment shape: one Ubuntu VPS, per-repo docker-compose stacks, a single nginx
gateway routing by hostname to internal-only container ports, GHCR image push +
SSH deploy on `workflow_dispatch`.

## Phases

| Phase | Date | What landed |
|---|---|---|
| **0** | 2026-09-22 | Repo scaffolding, zero behaviour change. Three Dockerfiles + compose + CI; empty `dataplane/` (`/health` only) and `worker/`; the `KUIS-FE` skeleton. Fixed `ALLOWED_HOSTS` (was hardcoded `[]`, which rejects everything once `DEBUG=False`) and added `STATIC_ROOT`. Django still served 100% of traffic |
| **1** | 2026-09-22 | JWT auth. DRF + SimpleJWT + blacklist, custom `is_staff`/`username`/`email` claims, a separate `JWT_SECRET`, FastAPI stateless verification proven on a new protected `/api/v1/whoami` (`/health` stays deliberately open), KUIS-FE login + cookie proxy + single-flight refresh-on-401 retry |
| **2** | 2026-09-22 | FastAPI skeleton, all 5 repository ABCs, and the fast-KWIC port — generalized from single-word to **phrase** search so one repository absorbs legacy KWIC too. Parity-checked byte-for-byte against both Django KWIC exports. `test_schema_drift.py` added here, ahead of schedule |
| **3** | 2026-09-22 | Next.js MVP on fast-KWIC — the first true end-to-end proof, driven by a real headless browser (login → corpus fetch → search → shareable-URL reload → corrected mode). First `GET /api/corpora/` on Django |
| **4** | 2026-09-23 | Word frequency, collocations and n-grams ported together on one shared self-join query builder, parity-checked twice: exact set+order on an isolated fixture, and hand-computed expected values against the real `corpus_db` through a real browser |
| **5** | 2026-09-24 | Tier 1/2 schema optimisation — `WordType`, `Token.word_type` replacing `Token.word`, `DocumentWordFreq`, `DocumentNgram`, applied to the real `corpus_db` in stages. Repositories swapped onto the new tables with services, routers and the frontend **genuinely untouched** — the repository-boundary payoff, verified by `git status` in KUIS-FE |
| **6** | 2026-09-25 | RBAC enforcement + Corpus/Document CRUD API + KUIS-FE management pages. The permission rule already existed and was already correct server-side; this exposed writes over the API for the first time |
| **6.5** | 2026-09-26 | KUIS-FE rebuilt on its own architecture playbook (`KUIS-FE/CLAUDE.md`) — TanStack Query, `hooks/`, `components/ui/` — plus full parity: public pages, register, dashboard, profile, persistent corpus selection, CSV export |
| **6.6** | 2026-09-26 | KWIC (Legacy) / KWIC (Fast) restored as distinct features, and the missing sort-by-position control added |
| **6.7** | 2026-09-28 | Content-hash upload dedup (migration `0010`, the unique constraint) |
| **6.8** | 2026-09-29 | Public/private corpora (`Corpus.is_public`, migration `0011`), enforced independently on both surfaces, 404-not-403 for a private corpus |
| **7** | 2026-09-29 | Error Analytics, a new build rather than a port: Error Frequency + EPIC behind one flat OR-combined code filter, a decoupled `/api/v1/taxonomy`, three models in migration `0012`, and the nested-`<segment>` tokenizer fix. The taxonomy blocker was resolved by `info/error.xml` arriving in-repo |
| **7.5** | 2026-10-02 | User management — Admins manage Users, Super Admins manage Admins. `UserProfile` (migration `0013`), `main/permissions.py`, `main/api_users.py`, `is_superuser` added to the JWT |
| **7.6** | 2026-10-02 | Error Summary hierarchical breakdown — `by_node` roll-up alongside the original `by_category`, plus the expandable tree |
| **7.7** | 2026-10-02 | Document metadata catalogue + secondary filters (migration `0014`). University/year/grade/name-code facets composing with corpus selection on every analysis feature, both surfaces |
| **7.8** | 2026-10-13 | Author (person-level) metadata + `gender`/`mother_tongue` facets (migration `0015`). Backend and the Django surface; KUIS-FE controls pending |
| **8** | — | ⬜ **Not started.** Caching (Tier 4) + retiring the superseded Django live-parse views |

Test-suite growth, for scale: 29 Django tests at Phase 0 → 229 today, plus 171
data plane tests.

## Notable course corrections

- **`DocumentNgram` uses a plain `JSONField` for `word_type_ids`**, not the
  originally designed `Ngram` dimension table with an `ArrayField` — `ArrayField`
  is Postgres-only and the Django suite deliberately runs on SQLite.
- **Error analytics shipped as one flat OR-combined code filter**, not the planned
  `path__startswith` subtree axis AND-combined with a category axis. Dropped once
  the real tree-picker UI existed: AND-across-branches isn't how anyone expects a
  checkbox tree to behave.
- **`AuthorSurvey` was built and removed before 7.8 shipped.** It stored all 20
  survey columns per occasion for a hypothetical future facet; nothing ever read
  it, so it was a second copy of the CSV free to drift from the first. The CSVs
  are tracked in git, so re-importing is the entire cost of getting the data back.
- **The access-token cookie is `httpOnly: false` on purpose** (settled in Phase 1).
  Cookies aren't sent cross-origin automatically regardless, so a Client Component
  must read the token to attach it as a bearer header; httpOnly blocked that for no
  real gain given the 15-minute lifetime. The refresh token stays httpOnly, scoped
  to `/api/auth`.
- **CORS was a gap no earlier phase could have caught.** curl neither sends nor
  enforces `Origin`, so every test before Phase 3's real browser run would have
  looked identical whether CORS was configured or not.
- **Two Next.js auth bugs found only against a live Django**: the refresh route
  saved only the `access` field, but `ROTATE_REFRESH_TOKENS=True` also returns a
  **new** refresh token and blacklists the old one (every session's *second*
  refresh would have broken); and the logout route deleted the refresh cookie
  without its original `path: "/api/auth"`, so it silently survived logout.
- **`worker/tasks/maintenance.py` was designed but never written.** `retokenize`
  stays a management command, now fixed to also refresh Tier-2 aggregates instead
  of leaving them stale. There is still no automatic staleness check.
- **The Tier-4 cache key is wider than planned** — the metadata filter (7.7) and
  its author facets (7.8) are additional scoping axes, so any key must cover all
  five facets plus the `__none__` sentinel per facet, or a filtered request will
  serve an unfiltered result.

## Infrastructure still outstanding

- **Phase 8 in full** — caching, and retiring the superseded Django views.
- **`/opt/platform` server-side compose entries and nginx routes** for the three
  KUIS images — outside any repo, on the VPS itself.
- **KUIS-FE has no Dockerfile, compose file or CI** — the last Phase-0 item.
- **`docker-compose.yml` caveat**: the real `.env`'s `SECRET_KEY` contains a `$`,
  which Compose's variable interpolation partially swallows. Escape it as `$$` or
  regenerate the key without special characters