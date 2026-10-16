# KUIS — Developer Onboarding

> Start here. Get a machine running, understand the configuration, and know
> what a handover has to decide. Describes what exists — not a redesign.
>
> **The other three docs:**
> - `docs/feature-list.md` — every feature, the architecture overview, the
>   codebase map, RBAC, and how to add a feature.
> - `docs/data-processing.md` — storage, the indexing pipeline, the metadata
>   catalogues, how queries are built, performance limits and planned work.
> - `docs/CHANGELOG.md` — what landed when.
>
> For setting up a machine from zero with Windows commands and a troubleshooting
> table, `info/new_device.md` is more detailed than §4 here.

---

## 1. The one-paragraph version

**KUIS** is a corpus-linguistics application for an Indonesian-language learner
corpus. Admins upload XML files (plain text plus `<segment>` error annotations
with corrections) and group them into named **corpora**; registered users run
reports across any multi-select of those corpora — word frequency, collocations,
n-grams, KWIC, and error analytics — over the text as written, each with a
secondary per-document metadata filter.

```
KUIS/                                   KUIS-FE/
├── config/  main/     Django  :8000    └── Next.js 16 / React 19  :3000
├── dataplane/         FastAPI :8001
├── worker/            Celery  (Redis broker)
└── one PostgreSQL database, schema owned by Django's migrations
```

Four processes, two repositories, **two live user interfaces** (server-rendered
Django pages *and* KUIS-FE on a JSON/JWT API — neither is deprecated, and they are
not at feature parity in either direction). Django owns the schema and is the
auth/RBAC source of truth; FastAPI is a read-only analysis data plane verifying
Django's JWTs statelessly; the Celery worker is the **only writer** of every
derived table. Details in `docs/feature-list.md` §2.

---

## 2. Configuration

One root `.env`, read by all three backend processes: Django via
`django-environ`, FastAPI via `pydantic-settings` (`dataplane/core/config.py`),
the worker inheriting Django's `os.environ` through `django.setup()`.

| Env var | Read by | Feeds | Default |
|---|---|---|---|
| `SECRET_KEY` | Django | `SECRET_KEY` | none (**required**) |
| `JWT_SECRET` | Django **+ FastAPI** | `SIMPLE_JWT['SIGNING_KEY']` / `decode_access_token`. Deliberately separate from `SECRET_KEY`: rotating one should never force rotating the other | none (**required by both**) |
| `DEBUG` | Django, FastAPI | `DEBUG` (bool) | `False` |
| `ALLOWED_HOSTS` | Django | `ALLOWED_HOSTS` (comma-separated) | `[]` — **required once `DEBUG=False`** |
| `DB_ENGINE` | Django | `DATABASES.default.ENGINE` | `django.db.backends.postgresql` |
| `DB_NAME` / `DB_USER` / `DB_PASSWORD` | Django, FastAPI | credentials. FastAPI composes `postgresql+asyncpg://…` from these rather than taking a separate URL — same instance, same schema | none (**required**) |
| `DB_HOST` / `DB_PORT` | Django, FastAPI | host/port | `localhost` / `5432` |
| `REDIS_URL` | worker | Celery broker **and** result backend | `redis://localhost:6379/0` |
| `CORS_ALLOWED_ORIGINS` | Django **+ FastAPI** | origins allowed to call `/api/*` — KUIS-FE's origin, not the backend's. One comma-separated string shared by both services | `http://localhost:3000` |
| `SEED_ADMIN_EMAIL` / `SEED_ADMIN_PASSWORD` | `seed` | the super admin account | none (skips it if blank) |
| `SEED_USER_EMAIL` / `SEED_USER_PASSWORD` | `seed` | the non-admin account | none (skips it if blank) |
| `CELERY_TASK_ALWAYS_EAGER` | worker | `1` runs tasks synchronously in-process, zero broker connections. **Required by both test suites** | unset |

Django-side specifics: `STATIC_ROOT = BASE_DIR / 'staticfiles'` (for
`collectstatic` on the Docker path); `CORS_URLS_REGEX = r'^/api/.*$'` — only the
API surface needs CORS, the server-rendered pages are same-origin navigations;
`SIMPLE_JWT` uses HS256 with a 15-minute access token, a 7-day refresh token,
`ROTATE_REFRESH_TOKENS` and `BLACKLIST_AFTER_ROTATION`. `TokenObtainPairView`
calls Django's normal `authenticate()`, so `UsernameOrEmailBackend` — login by
username *or* email — is inherited by the API for free.

`EMAIL_BACKEND` is still the **console backend**: the Contact form "sends" mail
only to the dev console. There is **no caching layer** (Tier 4 / Phase 8, not
started).

### Dependencies

**One `requirements.txt` for all three backend processes**, ~20 packages in four
labelled blocks:

- Django: `Django==6.0.4`, `django-environ`, `psycopg2-binary`, `asgiref`,
  `sqlparse`, `tzdata`
- FastAPI data plane: `fastapi`, `uvicorn[standard]`, `pydantic-settings`,
  `SQLAlchemy`, `greenlet`, `asyncpg`, `PyJWT`
- Django JWT/API: `djangorestframework`, `djangorestframework-simplejwt`,
  `django-cors-headers`
- Worker: `celery`, `redis`
- Tests: `pytest`, `pytest-asyncio`, `httpx`

No Pipfile or pyproject.toml. `INSTALLED_APPS` adds
`django.contrib.humanize`, `rest_framework`, `rest_framework_simplejwt`,
`rest_framework_simplejwt.token_blacklist`, `corsheaders`. **The server-rendered
interface itself adds no dependency** — static files are served by
`django.contrib.staticfiles` in development.

Frontend (`KUIS-FE/package.json`): `next@16.3.5`, `react@19.2.8`,
`@tanstack/react-query`, `react-hook-form` + `@hookform/resolvers`, `zod`,
`sonner`, `tailwindcss@4`.

---

## 3. Developer quick start

### Backend (`KUIS`)

```bash
git clone <repo> && cd KUIS
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\Activate.ps1
pip install -r requirements.txt

cp .env.example .env
# set SECRET_KEY and JWT_SECRET (two different generated values), the DB_*
# vars against a local Postgres database you've created, and
# SEED_ADMIN_EMAIL/PASSWORD + SEED_USER_EMAIL/PASSWORD. Your own .env, never
# committed.

python manage.py migrate
python manage.py seed                      # not optional — see §4
python manage.py load_error_taxonomy       # before any upload — see §4
python manage.py load_metadata_catalogue
python manage.py load_author_metadata
```

Then **three terminals**, each with the venv activated:

```bash
python manage.py runserver                               # :8000  Django
uvicorn dataplane.main:app --reload --port 8001          # :8001  FastAPI
celery -A worker.celery_app worker --pool=solo --loglevel=info   #    worker
```

Health checks: `http://127.0.0.1:8000/` · `http://127.0.0.1:8001/health` ·
`http://127.0.0.1:8001/docs`.

**`--pool=solo` is not optional here**, and never start a second worker alongside
a running one. The default prefork pool is broken in this environment (Python
3.14, celery 5.6.0, macOS arm64) — forked children never run
`setup_worker_optimizations`, so every task dies with
`ValueError: not enough values to unpack` *before* its body executes, with no
traceback from project code. Windows needs the same flag for its own reasons.

### Frontend (`KUIS-FE`, sibling directory)

```bash
cd ../KUIS-FE
cp .env.example .env.local      # must match the ports above
npm install
npm run dev                     # :3000
```

### First run-through

1. Open `http://127.0.0.1:8000/` and log in as the seeded admin
   (`SEED_ADMIN_EMAIL`; username or email both work).
2. **Corpora** → *Create New Corpus* → name it, set public/private, upload XML
   from `info/*.xml`. Watch the worker terminal: `index_document` must run, then
   the chained aggregate and error-annotation tasks. **If nothing appears there,
   Redis or the worker is down** — fix that first, or the document stays at 0
   tokens and is invisible to every analysis feature.
3. **Analysis** → tick a corpus → Word Frequency / Collocations / N-grams / KWIC.
   This surface parses XML live, so it works even with the data plane down.
4. Open `http://localhost:3000`, log in with the same credentials, and run Word
   Frequency there. A result proves the whole chain: Next.js → Django (issues the
   token) → FastAPI (verifies it statelessly) → Postgres.
5. `/analysis/error-analytics` in the frontend — **only** there, no
   server-rendered equivalent. `info/KUIS2023FUA201-Eror.xml` carries real
   `<segment>` annotations, so Error Frequency, EPIC and the Summary tree should
   all be populated.
6. Try the **metadata filter** in the context bar on any analysis page, including
   a facet's "(no value)" option.
7. Log in as the seeded non-admin (`SEED_USER_EMAIL`) in a separate session to
   confirm the upload/create/edit/delete/assign affordances are hidden *and*
   blocked, private corpora 404, and `/users` + `/admins` are inaccessible.

### Tests

```bash
# Django — 229 tests, SQLite, no Postgres needed
CELERY_TASK_ALWAYS_EAGER=1 DB_ENGINE=django.db.backends.sqlite3 DB_NAME=":memory:" \
  python manage.py test main

# Data plane — 171 tests, needs a real Postgres whose DB_NAME contains "test"
CELERY_TASK_ALWAYS_EAGER=1 DB_NAME=kuis_dataplane_test python manage.py migrate --noinput
CELERY_TASK_ALWAYS_EAGER=1 DB_NAME=kuis_dataplane_test python -m pytest dataplane/tests -v

# Frontend — no unit tests
cd ../KUIS-FE && npm run build && npm run lint
```

`CELERY_TASK_ALWAYS_EAGER=1` is **required** by both suites: fixtures call the
real `index_document.delay(...)`, and without eager mode 16 Django tests and most
data plane tests fail. The data plane fixture **refuses a `DB_NAME` without
"test" in it**, because it truncates `Document`/`Corpus` rows.
`ALLOWED_HOSTS` needs no override — Django's test client adds `testserver`.

---

## 4. Handover guide

### Must be configured manually

1. **Python 3.14.x + a fresh venv.** No `.python-version` pin exists in-repo;
   3.14.3 locally, `python:3.14-slim` in Docker, `3.14` in CI. Never reuse a venv
   copied from another machine.
2. **Node 22.x** for `KUIS-FE` (Next.js 16 requires ≥20.9). Never reuse a copied
   `node_modules`.
3. **`.env` at the `KUIS` root** — copy `.env.example`, then fill `SECRET_KEY` and
   `JWT_SECRET` with two **different** freshly generated values
   (`python -c "import secrets; print(secrets.token_urlsafe(64))"`), the `DB_*`
   vars, and the `SEED_*` vars. Both secrets are **required** and read at
   settings-load time, so Django won't start without either even if you only want
   the server-rendered pages.
4. **`.env.local` at the `KUIS-FE` root** — the two backend base URLs only. No
   secret belongs in that repo; it holds tokens, never the signing key.
5. **PostgreSQL 16** — one role + one empty database matching your `.env`.
   Optionally a second database whose name contains "test", for the data plane
   suite.
6. **Redis 7** — not optional. The worker is the only writer of every derived
   table.
7. **Admin + test user** — `python manage.py seed`. **Required**: without an
   `is_staff` account nobody can upload files or create corpora, which means no
   analysis is possible at all; the non-admin account is what lets you exercise
   the RBAC boundary without a second manual signup. Creates the admin as
   `is_staff` **and** `is_superuser`, so it is also your only Super Admin.
   Idempotent; `--update` resets password/flags. `createsuperuser` still works as
   a one-off alternative for the admin only.
8. **Reference data, in this order** — `load_error_taxonomy` **before any upload**
   (annotations resolve against that table at extraction time), then
   `load_metadata_catalogue`, then `load_author_metadata` (which links against the
   catalogue rows the previous step created). All three are idempotent and
   deliberately kept out of migration history, because taxonomy glosses,
   catalogue rows and survey data all get corrected independently of the schema.
   **`load_author_metadata` exits non-zero when the source CSVs contain a data
   error** — that is by design; run `--dry-run` first and read the report
   (`docs/data-processing.md` §7 lists the current findings).
9. **Deployment infrastructure** — `docker/{django,fastapi,worker}.Dockerfile`,
   `docker-compose.yml` and `.github/workflows/{ci,deploy}.yml` exist. The compose
   stack publishes **no host ports** (an externally deployed `nginx-gateway` is the
   only public entry point) and needs `docker network create app-shared` once per
   machine. **KUIS-FE has no Docker or CI files yet.** Revisit `ALLOWED_HOSTS`,
   `EMAIL_BACKEND` and `STATIC_ROOT`/`MEDIA_ROOT` before deploying with
   `DEBUG=False`.

### Handled automatically

- Schema and the two data migrations (`0006` legacy corpus, `0008` WordType
  backfill) via `python manage.py migrate`.
- Content-hash dedup at upload time, on all six upload paths.
- Metadata linking on document creation (and in the other direction at import
  time).
- Tokenization, Tier-2 aggregates and error-annotation extraction — **by the
  worker, asynchronously**, and only for newly created documents. **Not on later
  content edits.**
- Dependency installation via `pip install -r requirements.txt` (one file, all
  three backend processes) and `npm install`.

### Outstanding / known gaps

- **One stale `Author` row in `corpus_db`.** `load_author_metadata` upserts and
  never deletes, so the pre-cleaning `KUIS/WAKMOM` row survives and still claims
  a catalogue file. Fresh databases don't have it. See
  `docs/data-processing.md` §7.
- Phase 8 (caching, retiring the superseded Django live-parse views) is not
  started.
- `worker/tasks/maintenance.py` doesn't exist — `retokenize` stays a management
  command, and there is no automatic staleness check.
- **`Document.status = ready` does not guarantee the Tier-2 aggregates exist**,
  despite `worker/tasks/indexing.py`'s docstring saying it does — the status is
  set before the aggregate tasks are dispatched fire-and-forget. See
  `docs/data-processing.md` §3.
- KUIS-FE controls for the `gender`/`mother_tongue` facets are not built; the API
  serves them and the Django surface renders them.
- `KUIS-FE/README.md`'s phase table is stale (describes dashboard pages as stubs).
  `docs/CHANGELOG.md` is the authority.
- No browser click-through verification exists for the taxonomy tri-state checkbox
  visuals, the summary tree's disclosure toggles, or the metadata picker's
  rendering inside KUIS-FE's `ContextBar` — all were verified by server-rendered
  output and tests, not by clicking.

---

## 5. Flagged uncertainties

Do not treat these as fact without verifying.

- **`corpus_db` holds only 12 documents and 3 corpora**, 7 of them linked to a
  catalogue entry. So the metadata filter's behaviour at real corpus scale — 1,557
  catalogue rows against 12 uploaded files — has never been exercised; the facet
  coverage figures in `docs/data-processing.md` §7 are computed from the CSVs, not
  from analysable documents.
- **A project-wide `retokenize` has never been run for real** on `corpus_db`. A
  dry-run showed nothing currently needs it, which is not the same as having done
  it.
- **`DB_ENGINE=django.db.backends.sqlite3` works for the Django suite** (CI runs it
  that way) but is **not** a usable local dev database for the full stack: the data
  plane requires asyncpg/Postgres, with no SQLite path at all.
- **Whether uploaded files are expected to always follow the corpus XML schema, or
  plain prose is also legitimate.** The code supports both (the flat-tokenize
  fallback) but this has never been stated as an explicit product decision.
- **The six-upload-path count** (`main/views.py`'s
  `corpus_create`/`corpus_edit`/`upload_document`, `main/api_documents.py`, and
  `main/api_corpus.py`'s `CorpusListView`/`CorpusDetailView`). If you add a
  seventh, it **must** go through
  `main/document_ingest.py::get_or_create_document` or it silently skips dedup
  *and* metadata linking.
