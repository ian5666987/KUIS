# Running KUIS on a New Device

How to get a complete KUIS development environment running from scratch:
the Django back office, the FastAPI data plane, the Celery worker, and the
Next.js frontend.

> **This document replaced an earlier Django-only version.** KUIS stopped
> being a single Django process during the architecture revamp
> (`docs/new-architecture.md`). A device that only runs `manage.py
> runserver` can serve the server-rendered pages, but **every analysis
> feature in the Next.js frontend will fail**, because those read from the
> FastAPI data plane, and new uploads will never be tokenized, because that
> happens in the Celery worker. Set up all of it.
>
> Architecture background: `docs/ONBOARDING.md`. Feature write-ups:
> `docs/*.md`.

---

## 0. What you are setting up

Two git repositories, six processes:

```
                      ┌──────────────────────────┐
   browser  ─────────►│  KUIS-FE   (Next.js)     │ :3000
                      │  Next.js 16 / React 19   │
                      └─────┬───────────────┬────┘
                            │               │
       session-cookie pages │               │ JWT bearer, sent direct
       + JSON API    :8000  │               │ from the browser  :8001
                      ┌─────▼────────┐ ┌────▼──────────────────┐
                      │ Django       │ │ FastAPI "data plane"  │
                      │ back office  │ │ read-only analysis    │
                      │ auth, RBAC,  │ │ frequency, ngrams,    │
                      │ corpora,     │ │ collocations, KWIC,   │
                      │ uploads,     │ │ error analytics,      │
                      │ users, admin │ │ taxonomy, facets      │
                      └──┬────────┬──┘ └──────────┬────────────┘
                         │        │               │
               enqueues  │        │ writes        │ reads
                         ▼        ▼               ▼
              ┌──────────────┐ ┌───────────────────────────┐
              │ Redis  :6379 │ │ PostgreSQL         :5432  │
              │ Celery broker│ │ ONE database, one schema, │
              └──────┬───────┘ │ owned by Django's         │
                     │         │ migrations                │
                     │         └─────────────▲─────────────┘
              ┌──────▼───────┐               │
              │ Celery worker│───────────────┘
              │ tokenization,│   the only writer of
              │ aggregates,  │   Token / aggregate /
              │ error extract│   annotation tables
              └──────────────┘
```

Key facts that shape everything below:

- **One Postgres database, shared.** Django's migrations own the schema;
  FastAPI only reads it (SQLAlchemy Core table definitions mirroring the
  Django tables, no ORM models, no Alembic). So you run `migrate` from
  Django and never from FastAPI.
- **One `.env` file for all three backend processes**, at the `KUIS` repo
  root. Django reads it via `django-environ`, FastAPI via
  `pydantic-settings`, the worker inherits it through `django.setup()`.
  `JWT_SECRET` must be identical across Django (issues tokens) and FastAPI
  (verifies them) — using one file is what guarantees that.
- **`KUIS-FE` has its own `.env.local`** and holds no secrets at all, only
  the two backend base URLs.

| Process | Local port | Start command (from repo root) |
|---|---|---|
| PostgreSQL 16 | 5432 | OS service / Docker |
| Redis 7 | 6379 | OS service / Docker |
| Django | 8000 | `python manage.py runserver` |
| FastAPI | 8001 | `uvicorn dataplane.main:app --reload --port 8001` |
| Celery worker | — | `celery -A worker.celery_app worker --loglevel=info` |
| Next.js | 3000 | `npm run dev` (in `KUIS-FE`) |

---

## 1. Prerequisites

Install these first. Versions are what the project is actually built and
tested against (`docker/*.Dockerfile`, `.github/workflows/ci.yml`,
`KUIS-FE/package.json`):

| Tool | Version | Notes |
|---|---|---|
| **Python** | **3.14.x** | 3.14.3 locally, `python:3.14-slim` in Docker, `3.14` in CI. There is no `.python-version` pin in the repo. Any 3.14.x is fine. |
| **Node.js** | **22.x LTS** | Next.js 16 requires Node 20.9+; 22 is what's in use. |
| **PostgreSQL** | **16** | `postgres:16` in `docker-compose.yml`. pgAdmin optional but handy on Windows. |
| **Redis** | **7** | Celery broker *and* result backend. Not optional — see §5. |
| **git** | any | |

On Windows, during the Python installer, make sure **☑ Add python.exe to
PATH** is checked, then reopen your terminal and confirm:

```powershell
python --version      # Python 3.14.x
node --version        # v22.x
```

---

## 2. Get the two repositories

They are siblings. `KUIS-FE` references `KUIS/docs/*` and both expect that
layout:

```
your-workspace/
├── KUIS/        # backend: Django + FastAPI + Celery worker
└── KUIS-FE/     # frontend: Next.js
```

```bash
git clone <KUIS repo url>
git clone <KUIS-FE repo url>
```

If you **copied** the folders from another machine instead of cloning:

> **Delete the copied `KUIS/venv/` and `KUIS-FE/node_modules/`.**
> A virtualenv hardcodes absolute paths and the interpreter it was built
> against; `node_modules` contains compiled native binaries for the old
> machine's platform. Both are rebuilt from lock files in the steps below.
> `requirements.txt` and `package-lock.json` are the things worth copying —
> and they're in git anyway.

---

## 3. Backend: virtualenv + dependencies

From the `KUIS` folder (the one containing `manage.py`):

**macOS / Linux**
```bash
cd KUIS
python3 -m venv venv
source venv/bin/activate
```

**Windows (PowerShell)**
```powershell
cd KUIS
python -m venv venv
venv\Scripts\Activate.ps1
```

Your prompt should now start with `(venv)`. Confirm the environment is the
new one, not an inherited system Python:

```bash
python --version            # 3.14.x
which python                # Windows: where.exe python
                            # must point inside .../KUIS/venv/
```

Then install everything:

```bash
pip install -r requirements.txt
```

This is **one dependency file for all three backend processes** — Django,
FastAPI (`fastapi`, `uvicorn`, `SQLAlchemy`, `asyncpg`, `pydantic-settings`,
`PyJWT`), the worker (`celery`, `redis`), the API layer
(`djangorestframework`, `djangorestframework-simplejwt`,
`django-cors-headers`) and test tooling (`pytest`, `pytest-asyncio`,
`httpx`). It is ~20 packages, not the 6 the old Django-only project had.

If `psycopg2-binary` fails to build on Linux, install the Postgres client
headers first (`sudo apt-get install libpq-dev build-essential`) — the
Dockerfiles install exactly these.

---

## 4. PostgreSQL: role and database

You need one role and one **empty** database. Django's migrations create
every table; you never create tables by hand.

Defaults in `.env.example` (change the password):

```
DB_NAME=corpus_db
DB_USER=corpus_user
DB_PASSWORD=<your own>
DB_HOST=localhost
DB_PORT=5432
```

### Option A — `psql` (macOS / Linux)

```bash
psql -U postgres -c "CREATE USER corpus_user WITH PASSWORD 'your-password' LOGIN;"
psql -U postgres -c "CREATE DATABASE corpus_db OWNER corpus_user;"
```

### Option B — pgAdmin (Windows)

1. Open pgAdmin, connect to your server with the **PostgreSQL
   administrator** password you set when installing Postgres.
   *(That is a different password from `corpus_user`'s. Two separate
   things.)*
2. **Login/Group Roles** → right-click → **Create → Login/Group Role**
   - Name: `corpus_user`
   - **Definition** tab → Password: your password
   - **Privileges** tab → **Can login?** = Yes
3. **Databases** → right-click → **Create → Database**
   - Database: `corpus_db`, Owner: `corpus_user`

### Optional: a second database for the data plane test suite

`dataplane/tests/` needs a **real** Postgres (asyncpg has no SQLite
equivalent) and its own fixture **refuses to run unless `DB_NAME` contains
the string "test"** — a deliberate guard, because those fixtures truncate
`Document`/`Corpus` rows. If you intend to run that suite (§11), create it
now:

```bash
psql -U postgres -c "CREATE DATABASE kuis_dataplane_test OWNER corpus_user;"
```

---

## 5. Redis

The Celery worker is **not** optional scaffolding any more. Uploading a
document enqueues `index_document`, which tokenizes it, computes the Tier-2
aggregates (`DocumentWordFreq`, `DocumentNgram`) and extracts error
annotations. With no broker reachable, the upload itself still succeeds but
**the document stays untokenized and invisible to every analysis feature**.

```bash
# macOS
brew install redis && brew services start redis

# Linux
sudo apt-get install redis-server && sudo systemctl enable --now redis-server

# Windows — no official native build; use one of:
docker run -d --name kuis-redis -p 6379:6379 redis:7-alpine
# or run Redis inside WSL2

# verify, any platform
redis-cli ping     # PONG
```

---

## 6. The `.env` file (backend)

```bash
cp .env.example .env
```

Then fill it in. `SECRET_KEY` and `JWT_SECRET` have **no defaults** — Django
refuses to start without either, even if you only plan to use the
server-rendered pages, because both are read at settings-load time.

```bash
# generate two different values
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

| Variable | Read by | What to put |
|---|---|---|
| `SECRET_KEY` | Django | A fresh generated value. **Required.** |
| `JWT_SECRET` | Django **and** FastAPI | A second, different generated value. **Required.** Must match across both services — one file, so it does. |
| `DEBUG` | Django, FastAPI | `True` for local dev. |
| `ALLOWED_HOSTS` | Django | Leave empty for local dev. **Required once `DEBUG=False`.** Django's test client adds `testserver` by itself. |
| `DB_ENGINE` | Django | `django.db.backends.postgresql` |
| `DB_NAME` / `DB_USER` / `DB_PASSWORD` | Django, FastAPI | Match §4. **Required.** FastAPI composes its own async DSN (`postgresql+asyncpg://…`) from these same vars rather than taking a separate URL. |
| `DB_HOST` / `DB_PORT` | Django, FastAPI | `localhost` / `5432` |
| `REDIS_URL` | Celery worker | `redis://localhost:6379/0` |
| `CORS_ALLOWED_ORIGINS` | Django, FastAPI | `http://localhost:3000` — the **frontend's** origin, not the backend's. Both services need it; the browser calls both directly. |
| `SEED_ADMIN_EMAIL` / `SEED_ADMIN_PASSWORD` | `seed` command | Any local dev credentials. Creates the Super Admin. |
| `SEED_USER_EMAIL` / `SEED_USER_PASSWORD` | `seed` command | Any local dev credentials. Creates a plain user, for exercising the RBAC boundary. |

`.env` is gitignored. It never holds real credentials.

---

## 7. Database schema, accounts, and reference data

Still in the activated venv, in the `KUIS` folder, **in this order**:

```bash
# 1. Schema. Migrations 0001-0014; 0006 is a data migration that sweeps
#    pre-existing documents into "Ungrouped (legacy import)".
python manage.py migrate

# 2. Accounts. NOT OPTIONAL.
#    Creates the Super Admin (is_staff + is_superuser) from SEED_ADMIN_*
#    and a plain user from SEED_USER_*. Without an is_staff account nobody
#    can upload a file or create a corpus, so no analysis is possible at
#    all. Idempotent; --update resets password/flags on existing accounts.
python manage.py seed

# 3. Error taxonomy. ~90 Indonesian error codes from info/error.xml into
#    ErrorTaxonomyNode. RUN THIS BEFORE UPLOADING ANY DOCUMENT: error
#    annotations are resolved against this table at extraction time, and a
#    document uploaded before it is loaded gets annotations with a NULL
#    taxonomy_node. Idempotent (replaces the whole table each run).
python manage.py load_error_taxonomy

# 4. Metadata catalogue. 1,557 entries from metadata/*.csv into
#    DocumentMetadata, linked to uploaded documents by match key.
#    Idempotent and upserting; safe before any file exists, and safe to
#    re-run after uploads. Use --relink to only re-resolve the FKs.
python manage.py load_metadata_catalogue

# 5. Sanity check
python manage.py check
python -m django --version          # 6.0.4
python -c "from dataplane.main import app; assert app"      # FastAPI imports
python -c "from worker.celery_app import app; assert app"   # worker imports
```

Steps 3 and 4 are **reference-data loads, not migrations** — deliberately
kept out of migration history, because taxonomy glosses and catalogue rows
get corrected independently of the schema.

### Other management commands (not needed on a fresh device)

These exist for repairing or rebuilding derived data on an **existing**
database. On a brand-new empty database there is nothing for them to do.

| Command | What it does |
|---|---|
| `retokenize [--dry-run]` | Rebuilds every `Token` row from current `Document.content`, and refreshes the Tier-2 aggregates. Run after changing tokenization logic or editing a document's content — token rows are built at upload and do **not** follow later content edits. |
| `backfill_tier2 [--all]` | Computes `DocumentWordFreq`/`DocumentNgram` for documents that have tokens but no aggregates. Does not rebuild tokens. |
| `backfill_error_annotations [--all]` | Extracts `ErrorAnnotation`/`DocumentErrorFreq` for documents uploaded before the error-analytics feature (or before the taxonomy was loaded). Does not rebuild tokens. |
| `backfill_content_hash` | Recomputes `content_hash` with the canonicalized formula. Only relevant to databases predating migration `0010`; reports collision groups rather than merging them. |
| `load_metadata_catalogue --relink` | Re-resolves catalogue→document FKs without reading any CSV. |

---

## 8. Start the three backend processes

Three terminals, **each with the venv activated**, all from the `KUIS`
folder.

**Terminal 1 — Django back office** (session-cookie pages, `/admin/`, and
the whole `/api/` JSON surface):
```bash
python manage.py runserver        # http://127.0.0.1:8000/
```

**Terminal 2 — FastAPI data plane** (all analysis reads):
```bash
uvicorn dataplane.main:app --reload --port 8001
# http://127.0.0.1:8001/health        -> {"status": "ok"}
# http://127.0.0.1:8001/docs          -> interactive OpenAPI docs
```

**Terminal 3 — Celery worker** (tokenization, aggregates, error extraction):
```bash
celery -A worker.celery_app worker --loglevel=info
# should log "connected to redis://localhost:6379/0" and list its tasks
```

> **Port 8001 is not arbitrary** — `KUIS-FE/.env.local` points
> `NEXT_PUBLIC_FASTAPI_API_URL` at it. The Dockerfiles use 9415 internally
> for both Django and FastAPI, which is fine there because each runs in its
> own container and is resolved by container name.

> **Windows and the worker.** Celery's default prefork pool does not work on
> Windows. If the worker starts but never executes a task, add
> `--pool=solo`:
> `celery -A worker.celery_app worker --loglevel=info --pool=solo`

---

## 9. Frontend: Next.js

In a fourth terminal, from the `KUIS-FE` folder (no venv here):

```bash
cd ../KUIS-FE
cp .env.example .env.local
npm install
npm run dev                      # http://localhost:3000
```

`.env.local` holds only the two backend base URLs, and must agree with the
ports you started in §8:

```
NEXT_PUBLIC_DJANGO_API_URL=http://localhost:8000
NEXT_PUBLIC_FASTAPI_API_URL=http://localhost:8001
```

No JWT secret belongs in this repo. The frontend only ever **holds** tokens;
Django signs them and FastAPI verifies them with a secret neither this repo
nor the browser ever sees.

If you change `.env.local`, restart `npm run dev` — `NEXT_PUBLIC_*` values
are inlined at build time, not read at runtime.

---

## 10. First login and smoke test

Order matters here: the data plane can only answer about documents the
worker has already indexed.

1. **Django pages** — open `http://127.0.0.1:8000/` and log in as the
   seeded admin (`SEED_ADMIN_EMAIL`; username or email both work).
2. **Upload a corpus file** — *Corpora* → *Create New Corpus* → name it and
   upload `info/KUIS2022NAMI3.xml` and/or `info/KUIS2023FUA201-Eror.xml`.
   Watch Terminal 3: you should see `index_document` run, then the chained
   aggregate and error-annotation tasks. If nothing appears there, Redis or
   the worker is not up — fix that before going further.
3. **Django analysis** — *Analysis* → tick the corpus → run Word Frequency.
   This surface parses XML live and does **not** depend on FastAPI, so it
   works even if the data plane is down.
4. **Frontend** — open `http://localhost:3000`, log in with the same
   credentials, then *Analysis* → *Word Frequency*. This one reads the
   indexed tables **through FastAPI**, so a result here proves the whole
   chain: Next.js → Django (token) → FastAPI (verifies it) → Postgres.
5. **Error analytics** — `/analysis/error-analytics` in the frontend. This
   feature exists **only** on the Next.js surface; there is no
   server-rendered equivalent. `KUIS2023FUA201-Eror.xml` has real
   `<segment>` annotations, so Error Frequency, EPIC and the Summary tree
   should all be populated.
6. **RBAC** — log in as `SEED_USER_EMAIL` in a private window. Upload and
   corpus create/edit/delete must be hidden *and* blocked (403), private
   corpora must 404, and `/users` and `/admins` must be inaccessible.

---

## 11. Running the tests

Two suites, two runners, two different database requirements.

**Django suite** — 184 tests, runs on in-memory SQLite, no Postgres needed:

```bash
CELERY_TASK_ALWAYS_EAGER=1 \
DB_ENGINE=django.db.backends.sqlite3 DB_NAME=":memory:" \
python manage.py test main
```

**Data plane suite** — needs a **real** Postgres database whose name
contains "test" — the optional second database at the end of §4:

```bash
CELERY_TASK_ALWAYS_EAGER=1 DB_NAME=kuis_dataplane_test \
python manage.py migrate --noinput      # Django owns the schema

CELERY_TASK_ALWAYS_EAGER=1 DB_NAME=kuis_dataplane_test \
python -m pytest dataplane/tests -v
```

> **`CELERY_TASK_ALWAYS_EAGER=1` is required by both.** The synchronous
> `post_save` tokenization signal is gone; test fixtures now call the real
> `index_document.delay(...)`, and eager mode runs it in-process with zero
> broker connections. Without it, 16 Django tests and most data plane tests
> fail. `.github/workflows/ci.yml` sets it for exactly this reason.

**Frontend**:
```bash
cd ../KUIS-FE && npm run build && npm run lint
```
(There is no unit-test suite in `KUIS-FE`.)

On Windows PowerShell, inline `VAR=value` prefixes don't work — use
`$env:CELERY_TASK_ALWAYS_EAGER="1"` on its own line first.

---

## 12. Alternative: Docker

`docker-compose.yml` brings up all five backend services (`kuis-postgres`,
`kuis-redis`, `kuis-django`, `kuis-fastapi`, `kuis-worker`). Two things to
know before using it:

- **No service publishes a host port.** The house convention is that a
  separately-deployed `nginx-gateway` is the only public entry point and
  reaches each service by container name. So `docker compose up` alone gives
  you nothing on `localhost` — fine for production, not for local
  development against a local Next.js dev server. Add a `ports:` mapping
  yourself if you want that.
- **The `app-shared` network is external** and must exist first:
  ```bash
  docker network create app-shared    # once per machine
  docker compose up --build
  ```

`KUIS-FE` has **no** Dockerfile or compose file yet — it is a separate stack
and that work is still outstanding.

For local development, the venv route in §3–§9 is the shorter path. A
reasonable hybrid is Docker for Postgres and Redis only, with the three
Python processes and Next.js run natively.

---

## 13. Troubleshooting

| Symptom | Cause |
|---|---|
| `ImproperlyConfigured: Set the SECRET_KEY environment variable` | No `.env`, or `SECRET_KEY`/`JWT_SECRET` empty. Both are read at settings-load time and have no defaults. |
| `django.db.utils.OperationalError: password authentication failed` | `DB_*` in `.env` doesn't match the role you created, or the role has **Can login?** off. |
| Upload succeeds but the file shows 0 tokens and never appears in analysis | Redis or the Celery worker is down. Check Terminal 3. Recover with `python manage.py retokenize`. |
| Worker starts, logs tasks, but nothing ever runs (Windows) | Celery's prefork pool. Add `--pool=solo`. |
| Frontend analysis pages show an error while Django pages work | FastAPI isn't running on 8001, or `NEXT_PUBLIC_FASTAPI_API_URL` disagrees with where you started it. Check `http://127.0.0.1:8001/health`. |
| Browser console: "blocked by CORS policy" | `CORS_ALLOWED_ORIGINS` must contain the **frontend's** origin (`http://localhost:3000`) and is needed by *both* Django and FastAPI. `curl` never exercises this — only a real browser does. |
| 401 from FastAPI with a token Django just issued | `JWT_SECRET` differs between the two. Using the single root `.env` for both is the fix. |
| Error analytics is empty although documents have `<segment>` annotations | `load_error_taxonomy` ran *after* the uploads, so `taxonomy_node` is NULL. Run `python manage.py backfill_error_annotations --all`. |
| Metadata filters show no values | `load_metadata_catalogue` not run, or no uploaded document matches a catalogue entry — facet values come only from entries that have a linked document. |
| `dataplane/tests` refuses to run | `DB_NAME` must contain "test". A guard, not a bug: those fixtures delete rows. |
| `ALLOWED_HOSTS` / `DisallowedHost` with `DEBUG=False` | Set `ALLOWED_HOSTS` in `.env`. It defaults to `[]`, which rejects every request. |
| Next.js doesn't pick up an `.env.local` change | `NEXT_PUBLIC_*` is inlined at build time. Restart `npm run dev`. |

---

## 14. Final layout

```
your-workspace/
│
├── KUIS/                      # backend repo
│   ├── .env                   # ← you create this (gitignored)
│   ├── manage.py
│   ├── requirements.txt       # all three backend processes
│   ├── venv/                  # ← you create this (never copied)
│   ├── config/                # Django settings/urls/wsgi/asgi
│   ├── main/                  # Django app: models, views, api_*, templates
│   ├── dataplane/             # FastAPI: routers/services/repositories/schemas
│   ├── worker/                # Celery app + tasks
│   ├── docs/                  # ONBOARDING.md + per-feature write-ups
│   ├── info/                  # error.xml taxonomy, sample corpus XML, this file
│   ├── metadata/              # metadata_2023.csv, metadata_2024.csv
│   └── docker/                # three Dockerfiles
│
└── KUIS-FE/                   # frontend repo
    ├── .env.local             # ← you create this (gitignored)
    ├── node_modules/          # ← npm install creates this (never copied)
    ├── app/                   # App Router pages + /api/auth route handlers
    ├── api/ hooks/ lib/ components/
    └── proxy.ts               # route guard (Next 16's renamed middleware.ts)
```

And the setup, as a sequence:

```
ONE-TIME, PER DEVICE
    ├── Install Python 3.14, Node 22, PostgreSQL 16, Redis 7
    ├── Clone KUIS and KUIS-FE as siblings
    ├── Delete any copied venv/ and node_modules/
    ├── KUIS:    python -m venv venv  →  activate  →  pip install -r requirements.txt
    ├── Postgres: create role corpus_user + empty database corpus_db
    ├── KUIS:    cp .env.example .env  →  fill SECRET_KEY, JWT_SECRET, DB_*, SEED_*
    ├── KUIS:    migrate → seed → load_error_taxonomy → load_metadata_catalogue
    └── KUIS-FE: cp .env.example .env.local  →  npm install

EVERY TIME YOU DEVELOP  (4 terminals)
    ├── 1  python manage.py runserver                                :8000
    ├── 2  uvicorn dataplane.main:app --reload --port 8001            :8001
    ├── 3  celery -A worker.celery_app worker --loglevel=info
    └── 4  npm run dev            (in KUIS-FE)                        :3000
         (Postgres :5432 and Redis :6379 running as services)
```
