# KUIS — Quick Start (fresh device)

Condensed from `info/new_device.md` (read that for the why, troubleshooting
depth, and Docker). Six processes, two repos. Do the steps in order.

---

## Once per device

**1. Install** — Python **3.14**, Node **22 LTS**, PostgreSQL **16**, Redis **7**, git.
On Windows tick *Add python.exe to PATH*. Verify: `python --version`, `node --version`.

**2. Clone the two repos as siblings** (don't copy `venv/` or `node_modules/` from another machine):
```bash
git clone <KUIS repo url>
git clone <KUIS-FE repo url>
```

**3. Create the database role + empty database** (Django's migrations make the tables):
```bash
psql -U postgres -c "CREATE USER corpus_user WITH PASSWORD 'your-password' LOGIN;"
psql -U postgres -c "CREATE DATABASE corpus_db OWNER corpus_user;"
```
*Windows:* do the same in pgAdmin — role `corpus_user` with **Can login? = Yes**, database `corpus_db` owned by it.

**4. Start Redis** and confirm `redis-cli ping` → `PONG`:
```bash
brew services start redis                  # macOS
sudo systemctl enable --now redis-server   # Linux
docker run -d --name kuis-redis -p 6379:6379 redis:7-alpine   # Windows
```

**5. Backend: venv + dependencies**
```bash
cd KUIS
python3 -m venv venv && source venv/bin/activate    # Windows: python -m venv venv; venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**6. Backend `.env`**
```bash
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(64))"   # run twice, two different values
```
Fill in: `SECRET_KEY` and `JWT_SECRET` (both required, no defaults), `DB_PASSWORD`
(match step 3), `SEED_ADMIN_EMAIL`/`SEED_ADMIN_PASSWORD`, `SEED_USER_EMAIL`/`SEED_USER_PASSWORD`.
Leave `DB_*` / `REDIS_URL` / `CORS_ALLOWED_ORIGINS` at their defaults.

**7. Schema + data, in this exact order**
```bash
python manage.py migrate                   # tables
python manage.py seed                      # admin + plain user — without this nobody can upload
python manage.py load_error_taxonomy       # MUST run before any upload
python manage.py load_metadata_catalogue   # metadata facets
python manage.py check                     # sanity
```

**8. Frontend**
```bash
cd ../KUIS-FE
cp .env.example .env.local        # defaults already point at :8000 and :8001
npm install
```

---

## Every time you run the app — 4 terminals

Postgres (:5432) and Redis (:6379) must be running as services. Terminals 1–3 are in
`KUIS` **with the venv activated** (`source venv/bin/activate`); terminal 4 is in `KUIS-FE`.

| # | Command | URL |
|---|---|---|
| 1 | `python manage.py runserver` | http://127.0.0.1:8000 |
| 2 | `uvicorn dataplane.main:app --reload --port 8001` | http://127.0.0.1:8001/health |
| 3 | `celery -A worker.celery_app worker --loglevel=info` | — (add `--pool=solo` on Windows) |
| 4 | `npm run dev` | http://localhost:3000 |

All four are required: terminal 2 serves every analysis page in the frontend, and
terminal 3 is what turns an uploaded file into searchable data.

---

## Verify it works

1. Log in at **http://127.0.0.1:8000** as `SEED_ADMIN_EMAIL`.
2. *Corpora → Create New Corpus* → upload `info/KUIS2023FUA201-Eror.xml`.
   Terminal 3 must show `index_document` running. If it doesn't, Redis or the worker is down — fix before continuing.
3. Open **http://localhost:3000**, log in, run *Analysis → Word Frequency* (proves Next.js → Django → FastAPI → Postgres).
4. Open */analysis/error-analytics* — should be populated.

---

## If something breaks

| Symptom | Fix |
|---|---|
| `Set the SECRET_KEY environment variable` | `.env` missing or `SECRET_KEY`/`JWT_SECRET` empty |
| `password authentication failed` | `DB_*` in `.env` ≠ the role from step 3, or **Can login?** is off |
| Upload works but file shows 0 tokens / never appears in analysis | Worker or Redis down → start terminal 3, then `python manage.py retokenize` |
| Worker logs tasks but never runs them (Windows) | Add `--pool=solo` |
| Frontend analysis errors while Django pages work | FastAPI not on :8001 → check `/health` |
| `401` from FastAPI with a fresh Django token | `JWT_SECRET` differs — both services must read the one root `.env` |
| Error analytics empty despite annotations | Taxonomy loaded after upload → `python manage.py backfill_error_annotations --all` |
| Browser "blocked by CORS policy" | `CORS_ALLOWED_ORIGINS` must be `http://localhost:3000` |
