# KUIS — Developer Onboarding Document

> Technical onboarding reference for KUIS as it stands today, based on a
> full review of the codebase. Describes what exists — not a redesign.
> The living plan/status tracker is `docs/new-architecture.md`; per-feature
> write-ups are the other files in `docs/`. For getting a machine running
> from zero, see `info/new_device.md`.

---

## 1. Project Overview

**KUIS** is a corpus-linguistics application for analyzing an Indonesian-language learner corpus. Admins upload XML files (plain text plus `<segment>` error annotations with corrections) and group them into named **corpora**; registered users then run reports across any multi-select of those corpora: word frequency, collocations, n-grams, KWIC (keyword-in-context), and error analytics (error frequency, EPIC, and a hierarchical error summary) — each with an "original vs. corrected" text toggle, and each additionally narrowable by a secondary per-document **metadata filter** (university / year / grade / name code).

### Four services, two repositories, two live user interfaces

```
KUIS/                                   KUIS-FE/
├── config/  main/     Django  :8000    └── Next.js 16 / React 19  :3000
├── dataplane/         FastAPI :8001
├── worker/            Celery  (Redis broker)
└── one PostgreSQL database, schema owned by Django's migrations
```

- **Django (`config/`, `main/`)** — the back office and the auth/RBAC source of truth. Serves *two* surfaces at once: the original server-rendered pages (session cookies) and a JSON/JWT API under `/api/` (`main/api_*.py`) that KUIS-FE consumes. Both are live; neither can rely on the other to enforce anything, which is why every access rule is implemented twice (§5).
- **FastAPI (`dataplane/`)** — a **read-only** analysis data plane. Every analysis feature in KUIS-FE reads from here, not from Django. It verifies Django-issued JWTs statelessly (shared `JWT_SECRET`, zero calls back to Django) and reads the same Postgres tables Django's migrations own, via SQLAlchemy Core table definitions (`dataplane/models/tables.py`) — **no ORM models, no Alembic**. Layered `routers/ → services/ → repositories/` behind ABCs in `repositories/base.py`.
- **Celery worker (`worker/`)** — the **only writer** of the derived tables: tokenization (`Token`, `WordType`), the Tier-2 aggregates (`DocumentWordFreq`, `DocumentNgram`), and error annotations (`ErrorAnnotation`, `DocumentErrorFreq`). Bootstraps Django (`django.setup()`) so tasks reuse `main.models.build_tokens()` rather than re-implementing tokenization against a second ORM. **An upload with no worker running leaves the document untokenized and invisible to every analysis feature.**
- **Next.js (`KUIS-FE`, sibling repo)** — the frontend being built out against both backends. Its own structure is governed by `KUIS-FE/CLAUDE.md`. Note its `README.md` phase table is stale (it still describes the dashboard pages as stubs); `docs/new-architecture.md` is the authority.

### Which surface has which feature

The two UIs are **not** at parity, in both directions:

| | Server-rendered Django | KUIS-FE (Next.js) |
|---|---|---|
| Frequency / collocations / n-grams / KWIC (legacy + fast) | ✅ (live XML parse or `Token` scan) | ✅ (via FastAPI, indexed) |
| CSV export of each of the above | ✅ `/export/` routes | ✅ |
| Corpus CRUD, bulk assign, upload, public/private | ✅ | ✅ |
| Metadata secondary filter | ✅ | ✅ |
| **Error analytics** (Error Frequency, EPIC, Summary) | ❌ **none** | ✅ only here |
| **User management** (Admins/Super Admins) | Django `/admin/` only | ✅ `/users`, `/admins` |

- **Server-rendered interface**: a self-contained stylesheet (`main/static/main/kuis.css`, design tokens + components) plus one optional progressive-enhancement script (`main/static/main/kuis.js`). **No framework, no CDN, no Bootstrap** — every page works with JavaScript disabled.
- **Auth**: Django's built-in `auth.User` (no custom user model), plus a `UserProfile` sidecar for block/soft-delete bookkeeping. Four access tiers driven by `is_staff` and `is_superuser` — see §5. Server-rendered pages use session cookies; the API issues JWTs (`djangorestframework-simplejwt`) carrying `is_staff`/`is_superuser` as claims.
- **Database**: one PostgreSQL instance via `psycopg2-binary` (Django) and `asyncpg` (FastAPI), configured through env vars (`django-environ` / `pydantic-settings`) reading **the same root `.env`**.
- **Deployment tooling**: `docker/{django,fastapi,worker}.Dockerfile` + `docker-compose.yml` (five services on an external `app-shared` network, no published host ports), and `.github/workflows/{ci,deploy}.yml`. KUIS-FE has no Docker/CI files yet.

---

## 2. Feature Inventory

Access column: **anyone** · **registered** (any logged-in active user) · **admin** (`is_staff`) · **super admin** (`is_superuser`) — see §5.

### Back office (Django)

| Feature | Access | Description | Key files |
|---|---|---|---|
| **Public pages** (Home/About/Contact) | anyone | Static pages; Contact sends mail via `ContactForm` (console backend) | `main/views.py` (`home`, `about`, `contact`), `main/forms.py` `ContactForm`, `main/templates/main/{home,about,contact}.html` |
| **Registration** | anyone | Signup via `UserCreationForm` subclass + required email (rejected if another account already uses it, since the email is a sign-in identifier); auto-logs in, redirects to dashboard | `main/views.py` (`register`), `main/forms.py` `RegisterForm`, `main/templates/registration/register.html` |
| **Login / Logout** | anyone | Custom `LoginView` subclass accepting a username *or* the account's email (case-insensitive) via `UsernameOrEmailBackend`; stock `LogoutView` (POST-only, via nav form) | `main/views.py` (`CustomLoginView`), `main/forms.py` `LoginForm`, `main/auth_backends.py`, `config/urls.py`, `main/templates/registration/login.html` |
| **Dashboard / Profile** | registered | Minimal authenticated landing pages | `main/views.py` (`dashboard`, `profile`), `main/templates/main/{dashboard,profile}.html` |
| **Corpus list / detail** | registered | Browse corpora, their files, token counts, and which corpora overlap. Private corpora are filtered out for non-staff; management buttons render only for admins | `main/views.py` (`corpus_dashboard`, `corpus_detail`, `_annotated_corpora`), `main/templates/main/corpus_{dashboard,detail}.html` |
| **Create / edit / delete corpus** | **admin** | Name + description, **public/private toggle**, tick existing files, and/or upload new XML files inline. Deleting a corpus keeps its files | `main/views.py` (`corpus_create`, `corpus_edit`, `corpus_delete`), `main/forms.py` `CorpusForm`, `main/templates/main/corpus_{form,confirm_delete}.html` |
| **Bulk assign** | **admin** | Many files → many corpora in one submit, from a modal on the corpus dashboard | `main/views.py` (`corpus_assign`), `main/templates/main/components/assign_modal.html` |
| **Document upload** | **admin** | Standalone single-file upload (paste text or a UTF-8 file). Content-hash deduped, then indexed asynchronously by the worker | `main/views.py` (`upload_document`), `main/document_ingest.py`, `main/forms.py` `DocumentForm`, `main/templates/main/upload_document.html` |
| **Public / private corpora** | **admin** sets, all read | `Corpus.is_public` (`default=True`). Public → visible to every registered user; private → staff only. A private corpus requested by a non-staff user is a **404, not a 403** — a 403 would confirm it exists. Enforced independently on both surfaces | `main/models.py` `Corpus.is_public`, `main/views.py`, `main/api_corpus.py`, migration `0011` |
| **Django Admin** | **admin** | `Document`, `Corpus`, `ErrorTaxonomyNode`, `ErrorAnnotation` and `DocumentErrorFreq` registered (`CorpusAdmin` uses `filter_horizontal` for the M2M). `UserProfile` and `DocumentMetadata` are **not** registered | `main/admin.py` |

### Analysis (both surfaces unless noted)

| Feature | Access | Description | Key files |
|---|---|---|---|
| **Analysis hub** | registered | Corpus multi-select + scope statistics + links into every feature; the selection is remembered in the session (Django) / persisted client-side (KUIS-FE) | `main/views.py` (`analysis_home`), `main/templates/main/analysis_home.html` |
| **Word Frequency** | registered | Ranked word counts with per-million normalisation; filter (contains/starts/ends/exact), sort by word or count, paginate | Django: `main/views.py` (`word_frequency`), `main/corpus_parsing.py`. FastAPI: `GET /api/v1/frequency` |
| **Collocations** | registered | Adjacent word-pair counts, same table controls | Django: `main/views.py` (`collocations`). FastAPI: `GET /api/v1/collocations` |
| **N-grams** | registered | n-length word tuples; `n` is a 2–5 segmented control (`?n=`), same table controls | Django: `main/views.py` (`ngrams`, `NGRAM_SIZES`). FastAPI: `GET /api/v1/ngrams` |
| **KWIC (Legacy)** | registered | Concordance search for a word **or phrase**, context window 2–10, sortable four ways (`center` = corpus order, the default · `left` · `right` · `document`), paginated, each line attributed to its file | Django: `main/views.py` (`kwic`, `_kwic_results`, `_sort_kwic`, `KWIC_SORTS`) — a live XML re-parse. FastAPI: `GET /api/v1/kwic?sort=` — indexed, and an unrecognized value is a 422 |
| **KWIC (Fast)** | registered | Same concordance view for a **single word form**, answered from the index instead of re-parsing; no sort control | Django: `main/views.py` (`kwic_search`, `_word_index_matches`, `_hydrate_word_index`). FastAPI: the same `/api/v1/kwic` with `sort=center` |
| **Error Frequency** | registered | **KUIS-FE only.** Word-frequency's shape but for erroneous *phrases* — `{phrase, frequency}`, filtered by any set of taxonomy codes | `GET /api/v1/error-frequency`; `dataplane/{routers,services,schemas}/error_analytics.py`, `dataplane/repositories/postgres/{_error_query,error_analytics_repository}.py` |
| **EPIC** (Error Phrase In Context) | registered | **KUIS-FE only.** KWIC's shape but centered on annotated errors: left/keyword/right context plus the error code and the correction text | `GET /api/v1/epic` |
| **Error Summary** | registered | **KUIS-FE only.** Expandable tree over the *whole* taxonomy; each row carries its count, its share of all errors, and its share of its parent. `count` is already subtree-inclusive — the frontend never sums descendants | `GET /api/v1/error-summary` (`by_node` + the original `by_category`), `_error_query.py::compute_error_summary` |
| **Taxonomy picker** | registered | Tri-state cascading checkbox tree over the ~90 error codes. Checking a category cascades to every leaf beneath it; checking nodes from several branches **unions** their results | `GET /api/v1/taxonomy`; `KUIS-FE/lib/taxonomy.ts`, `components/features/analysis/TaxonomyPicker.tsx` |
| **Metadata secondary filter** | registered | University / Year / Grade (checkbox facets) + name code (prefix). OR within a facet, AND across facets. Each facet has a **"(no value)"** option (`__none__`) that matches both a blank field *and* a document the catalogue doesn't cover. Composes with corpus selection on **every** analysis feature | `GET /api/v1/metadata-facets`; `main/metadata_catalogue.py`, `main/templates/main/components/metadata_picker.html`, `dataplane/repositories/shared.py::resolve_document_ids`, `KUIS-FE/lib/metadataFilter.ts` |
| **CSV export** | registered | Django: every analysis has an `/export/` route re-running the same query with the same filter and sort, unpaginated. KUIS-FE builds its own export URLs | `main/views.py` (`*_export_csv`, `_csv_response`, `_sorted_rows_for_export`) |

### User management (KUIS-FE + `/api/`, no server-rendered equivalent)

| Feature | Access | Description |
|---|---|---|
| **Users tier** — list/create/edit/block/unblock/soft-delete regular users | **admin** | `/api/users/…`, pages at `KUIS-FE/app/(dashboard)/users/`. Queryset is `is_staff=False`, excluding super admins and soft-deleted rows |
| **Admins tier** — the same five actions over admin accounts | **super admin** | `/api/admins/…`, pages at `KUIS-FE/app/(dashboard)/admins/`. Queryset is `is_staff=True, is_superuser=False` |
| **Block / unblock** | per tier | Sets `is_active` plus `profile.blocked_at/by`, and **blacklists outstanding refresh tokens** — `TokenRefreshView` does not re-check `is_active`, so without that a blocked user could still mint a fresh access token |
| **Soft delete** | per tier | Never a hard DB delete: `Document.user` is `CASCADE`, so a hard delete would take every document that account uploaded and all its derived analytics rows. Instead: deactivate, anonymize PII, record `deleted_at/by`, and exclude the row from every endpoint afterwards. Terminal — no undelete |

Super Admin accounts are **not manageable through this feature at all** (both tiers unconditionally exclude `is_superuser=True`); they stay seed/CLI-provisioned. `is_staff`/`is_superuser` are never read from a request body — they're server-set from *which endpoint was called*. Full write-up: `docs/user-management-plan.md`.

### Management commands (CLI)

| Command | Purpose |
|---|---|
| `seed [--update]` | Bootstraps one super admin (`is_staff`+`is_superuser`) and one non-admin user from the `SEED_*` vars in `.env`. Idempotent. **Required on a fresh database** — without an `is_staff` account nobody can upload or create corpora, so no analysis is possible at all |
| `load_error_taxonomy` | Loads the ~90-code error taxonomy from `info/error.xml` into `ErrorTaxonomyNode`. Replaces the whole table each run. **Run before uploading documents** — annotations resolve against this table at extraction time |
| `load_metadata_catalogue [--file X.csv] [--dry-run] [--relink]` | Loads/upserts `metadata/*.csv` (1,557 entries from 1,559 rows) into `DocumentMetadata` and links each row to its `Document`. Idempotent; `--relink` re-resolves FKs without reading any CSV |
| `retokenize [--dry-run]` | Rebuilds all `Token` rows from current `Document.content` **and** refreshes the Tier-2 aggregates. Run after changing tokenization logic or editing a document's content |
| `backfill_tier2 [--all]` | Computes `DocumentWordFreq`/`DocumentNgram` for documents that have tokens but no aggregates. Does not rebuild tokens |
| `backfill_error_annotations [--all]` | Extracts `ErrorAnnotation`/`DocumentErrorFreq` for documents uploaded before the taxonomy was loaded. Does not rebuild tokens |
| `backfill_content_hash` | Recomputes `content_hash` with the canonicalized formula. Only relevant to databases predating migration `0010`; reports collision groups rather than merging them |

---

## 3. Codebase / Functionality Map

```
config/
  settings.py   — all Django config: DB, JWT, CORS, seed vars (see §4)
  urls.py       — root routing: admin/, main.urls, login/logout, api/
  wsgi.py, asgi.py — standard, unmodified

main/                          # Django app — back office + both auth surfaces
  models.py            — Document, Corpus, WordType, Token, DocumentWordFreq,
                         DocumentNgram, ErrorTaxonomyNode, ErrorAnnotation,
                         DocumentErrorFreq, UserProfile, DocumentMetadata;
                         build_tokens()
  views.py             — every server-rendered view (see §2)
  urls.py              — page routing, split into /corpus/ and /analysis/
  api_urls.py          — every /api/ route, in one place
  api_auth.py          — KUISTokenObtainPairView: JWT with is_staff +
                         is_superuser claims
  api_account.py       — RegisterView, ContactView, ProfileView
  api_corpus.py        — CorpusList/Detail/Assign (public/private enforced)
  api_documents.py     — DocumentListView (?has_metadata=), DocumentUploadView
  api_users.py         — the two account tiers; _AccountQuerysetMixin +
                         shared base views + two-line subclasses
  permissions.py       — IsSuperAdminUser, ADMIN_PERMISSIONS,
                         SUPERADMIN_PERMISSIONS, assert_not_targeting_self
  document_ingest.py   — canonicalize_for_hash, compute_content_hash,
                         get_or_create_document — the ONE definition of the
                         dedup formula, imported by every upload site and
                         by the worker so they can never drift
  metadata_catalogue.py— extract_textfile_name, normalize_match_key,
                         match_key_for_document, link_catalogue_entry,
                         relink_entries, import_catalogue_rows, FACET_FIELDS,
                         UNSET
  corpus_parsing.py    — XML tokenizer: parse_document, extract_word_streams,
                         extract_error_annotations, extract_textfile_name,
                         looks_like_structured_xml
  auth_backends.py     — UsernameOrEmailBackend
  forms.py, admin.py, apps.py
  tests.py             — 184 tests
  templatetags/kuis.py — hidden_params / qs / sort_url / aria_sort tags,
                         per_million + bar_width filters (see §7)
  static/main/kuis.css — the whole interface: design tokens, then components
  static/main/kuis.js  — optional enhancements only; nothing depends on it
  management/commands/ — seed, load_error_taxonomy, load_metadata_catalogue,
                         retokenize, backfill_{tier2,error_annotations,
                         content_hash}
  migrations/          — 0001-0014 (0006 and 0008 are data migrations)
  templates/
    main/base.html               — layout, nav, messages, auth conditionals
    main/components/             — the shared UI vocabulary (see §7)
      context_bar.html           — corpus selection + text mode + metadata
      corpus_picker.html         — multi-select with search, bulk actions
      metadata_picker.html       — the secondary facet filter
      assign_modal.html          — bulk file→corpora assignment
      analysis_nav.html          — feature tabs
      frequency_results.html     — stats + toolbar + table + pagination
      concordance_results.html   — shared KWIC table for both engines
      concordance_modal.html, sort_header.html, pagination.html,
      state.html, mode_notice.html
    main/{analysis_home,word_frequency,collocations,ngrams,kwic,
          kwic_search}.html
    main/corpus_{dashboard,detail,form,confirm_delete}.html
    main/upload_document.html
    main/{home,about,contact,dashboard,profile}.html
    registration/{register,login}.html

dataplane/                     # FastAPI — READ-ONLY analysis data plane
  main.py              — app + CORS + router registration
  dependencies.py      — get_current_user, require_staff, get_db_session,
                         one get_*_service per repository
  core/config.py       — pydantic-settings over the SAME root .env; composes
                         postgresql+asyncpg:// from Django's DB_* vars
  core/db.py           — async engine + session factory
  core/security.py     — AuthenticatedUser, decode_access_token (PyJWT,
                         stateless, zero calls back to Django)
  models/tables.py     — SQLAlchemy Core Table mirrors of Django's tables.
                         NOT ORM models. Drift here is what
                         tests/test_schema_drift.py exists to catch
  repositories/base.py — every repository ABC + ErrorFilter / MetadataFilter
  repositories/shared.py — resolve_document_ids: the ONE point the corpus
                         selection and the metadata filter become an id list
  repositories/postgres/ — frequency, collocation, ngram, kwic,
                         error_analytics, taxonomy, metadata repositories +
                         the shared builders _ranked_query, _aggregate_query,
                         _ranked_pagination, _error_query
  services/            — one per feature + pagination.py
  schemas/             — response models, plus metadata.py (the one shared
                         *request* schema: MetadataFilterParams, Depends()-ed
                         by all seven analysis routers)
  routers/             — health, whoami, frequency, collocations, ngrams,
                         kwic, error_analytics, taxonomy, metadata
  tests/               — 20 files; needs a real Postgres (asyncpg)

worker/                        # Celery — the only WRITER of derived tables
  celery_app.py        — django.setup() then Celery(broker=REDIS_URL);
                         honours CELERY_TASK_ALWAYS_EAGER=1
  tasks/indexing.py    — index_document: tokenize + hash, then chains the
                         aggregate and error-annotation tasks
  tasks/aggregates.py  — compute_document_word_freq, compute_document_ngrams
  tasks/error_annotations.py — extract_document_error_annotations, which
                         calls compute_document_error_freq itself rather than
                         as a second unordered .delay()
  (tasks/maintenance.py does not exist — retokenize stays a CLI command)

metadata/              — metadata_2023.csv, metadata_2024.csv (tracked)
info/                  — NOT code: error.xml (the taxonomy source),
                         two sample corpus XML files, a proposal PDF,
                         informal notes, and new_device.md (setup guide)
docs/                  — this file, new-architecture.md (status tracker),
                         and one write-up per feature
docker/                — django / fastapi / worker Dockerfiles
```

**Representative flow — Word Frequency, server-rendered:**
`GET /analysis/frequency/` → `views.word_frequency` → `_require_selection` (bounces to the hub if nothing is selected) → `_get_selected_documents(request)` resolves session corpus ids **and the metadata facet params** to a `.distinct()` document queryset → `_count_words()` parses each document into one word list per document and folds them into a `Counter` → `_rank_counter()` applies the filter, sort and pagination shared by all three count analyses → renders `word_frequency.html`, which includes `components/context_bar.html` and delegates results to `components/frequency_results.html`. The `/export/` route runs the same pipeline unpaginated.

**Representative flow — Word Frequency, KUIS-FE:**
`/analysis/frequency` → `hooks/fastapi/useRankedSearch.ts` → `GET /api/v1/frequency` with the bearer token → `routers/frequency.py` (`Depends(get_current_user)`, `Depends(MetadataFilterParams)`) → `FrequencyService` → `PostgresFrequencyRepository` → `repositories/shared.py::resolve_document_ids` turns corpus ids + metadata filter into a document id list → `_ranked_query` sums the **precomputed `DocumentWordFreq` aggregate** over those ids. No XML is parsed at request time on this path.

**Representative flow — upload:**
`POST` on any of the upload paths → `document_ingest.get_or_create_document()` canonicalizes the content, hashes it, and `get_or_create`s on `content_hash` (race-safe via the unique constraint) → **if created**, `index_document.delay(doc.id)` and `link_catalogue_entry()` → worker tokenizes (`build_tokens` → `WordType` + `Token`), writes `content_hash`/`tokenized_hash`, then chains `compute_document_word_freq`, `compute_document_ngrams`, and `extract_document_error_annotations` (which itself calls `compute_document_error_freq`). **If reused**, none of that runs and the existing document's title/uploader are left alone.

---

## 4. Data & Architecture

### Models (`main/models.py`)

**Core**
- **`Document`**: `title`, `content` (raw XML/text), `uploaded_at`, `created_at`, `user` (FK→`User`, nullable, **`CASCADE`** — the reason account deletion is soft, §2), `token_count`, `token_count_corrected`, `status` (`ready`|`indexing`|`failed`), `content_hash` (**unique**, migration `0010`), `tokenized_hash`, `tokenizer_version`. Index on `(user, uploaded_at)`.
- **`Corpus`**: `name` (unique), `description`, `created_by` (FK→`User`, `SET_NULL`), `created_at`, `is_public` (`default=True`), and **`documents = ManyToManyField(Document, related_name='corpora')`**. The M2M is deliberate — a file may belong to several corpora, and the client has accepted that overlap.

**Tier 1 / Tier 2 — the indexed read path (migrations `0007`–`0009`)**
- **`WordType`**: `form` (unique). One row per distinct word form, project-wide.
- **`Token`**: `document` (FK, `related_name='tokens'`), **`word_type`** (FK→`WordType` — this *replaced* the old `Token.word` string column), `position`, `mode` (`original`|`corrected`). One row per token per mode per document. Tokenization is **per-document**, so corpus changes never require retokenizing.
- **`DocumentWordFreq`**: `(document, word_type, mode, count)` — precomputed word counts.
- **`DocumentNgram`**: `(document, n, word_type_ids, mode, count)`. `word_type_ids` is a plain **`JSONField`** holding an ordered `list[int]`, not an `ArrayField` — `ArrayField` is Postgres-only and the Django test suite deliberately runs on SQLite.

Both aggregates are **per-document grain**, which is why narrowing the document id list (corpus selection, metadata filter) just sums over fewer rows and needed no query rework anywhere.

**Error analytics (migration `0012`)**
- **`ErrorTaxonomyNode`**: `code` (unique), `parent` (self-FK), `path` (unique), `gloss`, `is_leaf`, `top_category`. Loaded from `info/error.xml`. ~90 rows. A leaf's `path` always ends with its own code, which is what lets `concat(';', path, ';') LIKE '%;code;%'` match **both** a direct leaf hit and every descendant of a checked category — one mechanism, no separate exact-match branch.
- **`ErrorAnnotation`**: one row per `<segment>` — `document`, `taxonomy_node` (`SET_NULL`, nullable), `raw_features` (the original string, kept for later re-resolution), `parent` (self-FK, resolved from the segment's explicit `parent=` attribute, **not** structural nesting — real data shows these diverge), `source_segment_id`, `start_position`, `end_position`, `original_text`, `correction_text`, `state`, `comment`.
- **`DocumentErrorFreq`**: `(document, taxonomy_node, count)` — used **only** by the summary view. Error Frequency itself queries `ErrorAnnotation` live, since phrase text isn't a bounded-enough dimension for an aggregate to help.

**Accounts & metadata (migrations `0013`, `0014`)**
- **`UserProfile`**: `user` (OneToOne, `related_name='profile'`), `blocked_at`/`blocked_by`, `deleted_at`/`deleted_by`. A **sidecar**, not a custom `AUTH_USER_MODEL` — swapping that 12 migrations in, with `Document.user`, `Corpus.created_by` and `token_blacklist` already pointing at `auth.User`, would be a large, high-risk change for four columns. Rows are created lazily via `get_or_create`, never backfilled; a profile-less account simply reads as "active, never blocked."
- **`DocumentMetadata`**: `source_filename`, `match_key` (**unique**), `document` (**OneToOne**, `SET_NULL`, nullable, `related_name='catalogue_entry'`), `university`, `year`, `grade`, `topic`, `topic_en`, `word_count`, `name_code`, `source_file`, `imported_at`. Indexes on `university`, `(year, grade)`, `name_code`. The FK lives on the **catalogue** side, not on `Document` — the catalogue is the authority and loads independently of upload order, so resolution runs in both directions. Every analysis field is nullable because the "(no value)" filter needs one representation of empty (a blank cell imports as `None`, never `''`).

### Corpus XML format (parsed by `main/corpus_parsing.py`)
```xml
<document>
  <header><textfile>KUIS/KUIS2023FUA201.txt</textfile><lang>indonesian</lang></header>
  <body>
    Plain text ... <segment id='104' features='eror;leksikal;kata;ktinf' Correction='tetapi'>tapi</segment> ...
  </body>
</document>
```
- `<segment>` marks an annotated error; `features` is a **semicolon-separated tag path** (`eror;<category>;<subcategory>;<leaf-code>`) whose depth varies by error type. The taxonomy is now documented in-repo: `info/error.xml`, loaded into `ErrorTaxonomyNode`.
- `<header><textfile>` is read by `extract_textfile_name` and is the **primary metadata match key** (§6). It deliberately does *not* gate on `looks_like_structured_xml`, whose `<body>` requirement is a tokenization concern, not a header one.
- `parse_document()` falls back to flat (non-XML-aware) tokenization when the content isn't parseable XML or has no `<body>`, and returns an `is_structured` flag for exactly that case; the UI names the affected files in corrected mode (`components/mode_notice.html`), where the fallback would otherwise look like an identical result for no reason.
- **Nested `<segment>` elements are handled.** They were silently dropped by the original tokenizer; fixed as part of the error-analytics work.

### Migrations
`0001`–`0004` build `Document`/`Token`. `0005` adds `Corpus` + its M2M. **`0006_legacy_corpus` is a data migration** sweeping every pre-existing document into a corpus named `"Ungrouped (legacy import)"`, because a document in no corpus is invisible to every analysis feature (reversible — the reverse deletes that corpus). `0007`–`0009` are the Tier-1/2 schema change (`WordType`, `Token.word_type` replacing `Token.word`, `DocumentWordFreq`, `DocumentNgram`), **`0008` being the backfill**. `0010`–`0014` are all pure additions: the `content_hash` unique constraint, `Corpus.is_public`, the three error-analytics models, `UserProfile`, `DocumentMetadata`.

> **Not yet applied to `corpus_db`**: migration `0014` (`DocumentMetadata`) and the catalogue import. See `docs/metadata-catalogue-plan.md` for the sequence.

### External services / background jobs
- **Redis + Celery are real and required.** `worker/tasks/` holds the actual indexing, aggregate and error-extraction tasks. The synchronous `post_save` tokenization signal that used to live in `main/models.py` is **fully removed** — the six upload sites call `index_document.delay(doc.id)` explicitly instead.
- `EMAIL_BACKEND` is still the console backend — the Contact form "sends" mail only to the dev console.
- No caching layer yet (Tier 4 / Phase 8, not started).

### Configuration

One root `.env`, read by all three backend processes: Django via `django-environ`, FastAPI via `pydantic-settings` (`dataplane/core/config.py`), the worker inheriting Django's `os.environ` through `django.setup()`.

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

Django-side specifics: `STATIC_ROOT = BASE_DIR / 'staticfiles'` (for `collectstatic` on the Docker path); `CORS_URLS_REGEX = r'^/api/.*$'` — only the API surface needs CORS, the server-rendered pages are same-origin navigations; `SIMPLE_JWT` uses HS256 with a 15-minute access token, a 7-day refresh token, `ROTATE_REFRESH_TOKENS` and `BLACKLIST_AFTER_ROTATION`. `TokenObtainPairView` calls Django's normal `authenticate()`, so `UsernameOrEmailBackend` — login by username *or* email — is inherited by the API for free.

### Dependencies (`requirements.txt`)
**One file for all three backend processes**, ~20 packages in four labelled blocks:
- Django: `Django==6.0.4`, `django-environ`, `psycopg2-binary`, `asgiref`, `sqlparse`, `tzdata`
- FastAPI data plane: `fastapi`, `uvicorn[standard]`, `pydantic-settings`, `SQLAlchemy`, `greenlet`, `asyncpg`, `PyJWT`
- Django JWT/API: `djangorestframework`, `djangorestframework-simplejwt`, `django-cors-headers`
- Worker: `celery`, `redis`
- Tests: `pytest`, `pytest-asyncio`, `httpx`

No Pipfile/pyproject.toml. `INSTALLED_APPS` adds `django.contrib.humanize`, `rest_framework`, `rest_framework_simplejwt`, `rest_framework_simplejwt.token_blacklist`, `corsheaders`. The server-rendered interface itself adds **no** dependency — static files are served by `django.contrib.staticfiles` in development.

Frontend (`KUIS-FE/package.json`): `next@16.3.5`, `react@19.2.8`, `@tanstack/react-query`, `react-hook-form` + `@hookform/resolvers`, `zod`, `sonner`, `tailwindcss@4`.

---

## 5. Access Control (RBAC)

Four tiers, driven entirely by Django's built-in `User.is_staff` and `User.is_superuser` flags. **There is still no role or permission table** — an admin is a user with `is_staff=True`, a super admin one with `is_superuser=True`.

| Tier | Who | Can do |
|---|---|---|
| **Anonymous** | not logged in | Nothing but Home, About, Contact, Register, Login. Every feature URL redirects to `/accounts/login/?next=…`. |
| **Registered** | any logged-in active user | All analysis features, the analysis hub, **public** corpus list/detail (read-only), dashboard, profile. |
| **Admin** | `is_staff=True` | Everything above, **plus** private corpora, XML upload, corpus create/edit/delete/assign, the public/private toggle, Django `/admin/`, and the **Users** management tier. |
| **Super Admin** | `is_superuser=True` | Everything above, **plus** the **Admins** management tier. Seed/CLI-provisioned only — no endpoint can create, view, edit, block or delete a super admin. |

The RBAC *rule* is unchanged by the user-management feature: it is still the single `is_staff` boolean for every analysis and corpus action. `is_superuser` gates exactly one thing — the Admins tier. **No analysis endpoint has a staff gate at all.**

**How it's enforced, surface by surface.** All three implementations are independent on purpose: all three are live simultaneously, so none can rely on another to filter.

*1 — Server-rendered pages* (`main/views.py`):

```python
def staff_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not (request.user.is_active and request.user.is_staff):
            raise PermissionDenied
        return view_func(request, *args, **kwargs)
    return wrapper
```

**Decorator order matters**: always `@login_required` *above* `@staff_required`. That way an anonymous visitor gets the login page (from `login_required`, which runs first), while a signed-in non-admin gets a plain **403** instead of being bounced to a login form they're already past. Reversing the order sends anonymous users to a confusing 403.

Admin-only views: `upload_document`, `corpus_assign`, `corpus_create`, `corpus_edit`, `corpus_delete`. Everything else user-facing is plain `@login_required`. Templates gate admin affordances with `{% if user.is_staff %}`, so non-admins never see those buttons — but the decorators are the actual boundary, not the template checks.

*2 — Django JSON API* (`main/api_*.py`, `main/permissions.py`): `main/api_auth.py` puts `is_staff` and `is_superuser` straight onto the JWT as claims. DRF views then gate with the shared constants from `main/permissions.py` — `ADMIN_PERMISSIONS` (`[IsAuthenticated, IsAdminUser]`) on corpus create/edit/delete, document upload, and `/api/users/…`; `SUPERADMIN_PERMISSIONS` (`[IsAuthenticated, IsSuperAdminUser]`) on `/api/admins/…`. `main/api_corpus.py` imports these rather than defining its own copy.

*3 — FastAPI data plane* (`dataplane/dependencies.py`): `get_current_user` decodes the token statelessly with the shared `JWT_SECRET` and never calls back to Django; `require_staff` checks the `is_staff` claim. Currently **no analysis router uses `require_staff`** — every endpoint is authenticated-only.

**Public/private corpora cut across all of this.** `Corpus.is_public` is enforced separately in `main/views.py` (`corpus_dashboard`, `corpus_detail`) and `main/api_corpus.py` (`CorpusListView`, `CorpusDetailView`). A private corpus requested by a non-staff user returns **404, not 403** — a 403 would confirm it exists.

**Account state** (`UserProfile`):
- **active** — `is_active=True`, `deleted_at IS NULL`.
- **blocked** — `is_active=False`, `deleted_at IS NULL`. Reversible. `JWTAuthentication.get_user()` re-checks `is_active` on every request, so a live access token dies immediately — but `TokenRefreshView` does **not**, which is why block and delete also blacklist outstanding refresh tokens.
- **deleted** — `is_active=False`, `deleted_at IS NOT NULL`. Terminal: excluded from every queryset, so the id 404s afterwards.

Use `python manage.py seed` (§8/§9) to get a super admin and a non-admin account for exercising the tiers against all three surfaces without hand-editing users in `/admin/`.

---

## 6. Edge Cases & Technical Considerations

### Scoping and selection
- **A document in no corpus is invisible to every analysis feature.** By design (analysis is corpus-scoped), which is exactly why migration `0006` exists. `corpus_dashboard` shows an "unassigned files" warning to admins so new orphans get noticed.
- **Overlap is deduped via `.distinct()`** in `_get_selected_documents`. Without it the M2M join returns a document once per matching corpus and it would be counted multiple times. Verified: selecting A∪B where B⊂A yields byte-identical output to selecting A alone.
- **Never flatten word lists across documents.** `_get_word_lists` returns *one list per document* precisely so collocation/n-gram/KWIC windows don't straddle a document boundary and invent pairs that exist in no real text. Any new sequence-based feature must fold per-document the same way.
- **The corpus selector needs its hidden `corpus_selection=1` marker.** Unchecking every box submits no `corpus` params at all, which is otherwise indistinguishable from "form not submitted" — without the marker the selection could never be cleared.
- **The selector also re-emits the current feature's filters** (`corrected`, `n`, `q`, `w`, `sort`, `word`, **and every metadata facet**) as hidden inputs. Drop those and changing corpora silently resets the n-gram size or wipes the KWIC query.
- **Stale session ids** are pruned in `_resolve_selected_corpus_ids` for *both* submitted and remembered ids — a stale browser tab can post a corpus id that has since been deleted.
- **Deleting a corpus never deletes its documents** — only the corpus row and its M2M links.

### The two write paths are not interchangeable
- **`Token`/aggregate rows are built by the worker, not by a signal.** The `post_save` signal is gone. The six upload sites call `index_document.delay()` explicitly, and **skip it when `get_or_create_document` reports the document was reused**. With no worker running, an upload succeeds and the document stays at 0 tokens, invisible everywhere. Recover with `python manage.py retokenize`.
- **`Token` rows still go stale on content edits.** Nothing re-indexes a document whose `content` is edited afterwards. `tokenized_hash` exists to detect exactly that (`tokenized_hash IS DISTINCT FROM content_hash`) but **that check is not implemented**. Run `retokenize` by hand.
- **`index_document` deletes before inserting**, so a re-run never doubles up; the same is true of the aggregate and annotation tasks.
- **`extract_document_error_annotations` calls `compute_document_error_freq` itself** rather than being a second `.delay()` from `index_document` — two unordered `.delay()` calls give no guarantee the annotations exist before the aggregate reads them.
- **`CELERY_TASK_ALWAYS_EAGER=1` is required by both test suites.** Fixtures call the real `index_document.delay(...)`; eager mode runs it in-process with zero broker connections. Without it, 16 Django tests and most data plane tests fail.

### Dedup, matching, and identity
- **Content-hash dedup reuses the existing `Document`**; a dedup hit never renames or reassigns it (`title`/`user` are in `get_or_create`'s `defaults`, so whichever upload won owns it). The hash input is *canonicalized* (BOM, CRLF→LF, surrounding whitespace, `ET.canonicalize()` for structured XML) but `Document.content` always stores the exact raw upload.
- **A dedup hit does not re-link metadata.** The returned document was linked when first created. Consequence: if two catalogue files hold byte-identical content, only one `Document` exists, only one entry can claim it, and the second stays unlinked — reported by the import as awaiting upload.
- **The metadata match key is the document's own `<header><textfile>`, falling back to `Document.title`** — basename, exactly one extension stripped, lowercased, trimmed. The header wins because it travels inside the file (survives renaming) and is the only thing reconciling the catalogue's `.txt` names with uploaded `.xml`. **No suffix-stripping heuristic exists anywhere**: `A2023B-Eror.xml` normalizes to `a2023b-eror`, *not* `a2023b`; the header is what resolves that case. The title fallback is weaker than it looks — on `upload_document` and `DocumentUploadView` the title is free text the user typed, so those match only by luck. Full rules: `docs/metadata-matching.md`.
- **A claimed catalogue entry is never reassigned, and only a NULL link is ever filled in** — re-importing is idempotent, and two documents mapping to one entry is a data question for a human.
- **`Correction` vs `correction` attribute casing.** Sample files use both; the walker only reads capital-C `Correction`, so lowercase-attribute segments contribute nothing to the corrected word stream.
- **Malformed XML degrades to flat regex tokenization, but not silently**: `parse_document`'s `is_structured` flag drives `components/mode_notice.html`. Unparseable content is never an upload failure, and a missed metadata match is reported, never raised.

### Error analytics
- **The taxonomy must be loaded before documents are uploaded.** `extract_document_error_annotations` resolves `raw_features` against `ErrorTaxonomyNode` at extraction time; with an empty taxonomy, `taxonomy_node` stays NULL (`raw_features` keeps the original string for later re-resolution). Fix with `backfill_error_annotations --all`.
- **The summary and the two search features disagree by design.** `resolve_leaf_node_ids` filters `is_leaf = TRUE`, but a `DocumentErrorFreq` row can legitimately point at a **non-leaf** node (`features="eror;leksikal"` is real data). So a non-leaf-resolved annotation is counted by the Summary but never matched by Error Frequency or EPIC. `self_count` is what makes that visible in the UI (`(N not sub-classified)`) instead of letting the arithmetic look broken.
- **`error-summary`'s `count` is already subtree-inclusive.** The frontend never sums descendants to render a parent — it only divides to get a percentage. The roll-up happens in Python in `_error_query.py::compute_error_summary`, not a recursive CTE, because `path` already spells out every ancestor's code.
- **`ErrorAnnotation.parent` is the segment's explicit `parent=` attribute, not structural nesting** — real data shows these diverge, and the attribute is authoritative.

### The metadata filter
- **It is applied at exactly one point per surface** — `dataplane/repositories/shared.py::resolve_document_ids` and `main/views.py::_get_selected_documents`. Both already resolved a corpus selection to documents, so **no repository, SQL builder, worker task or analysis view changed**, and all five Django `/export/` routes inherited the filter for free. Add the filter anywhere else and you have two places to keep in step.
- **A LEFT OUTER JOIN is what makes "(no value)" one expression instead of two branches.** Joined from the document side, `university IS NULL` is true for *both* an entry with a blank field and a document with no entry at all. There is no "documents without metadata" code path anywhere. Django's ORM reaches the same thing via `catalogue_entry__university__isnull=True`.
- **Integer facets get no `= ''` test** — comparing an integer column to an empty string is a type error in Postgres.
- **Junk on a numeric facet widens rather than 422s.** The filter is a navigation control, so a stale URL should never break the page. `year`/`grade` are declared `list[str]` and coerced in the service layer, because the `__none__` sentinel isn't an int.
- **`context_bar.html`'s `hidden_params exclude` must name every facet**, or the form submits each value twice (once hidden, once from the real checkbox).
- **Facet values come only from entries with a linked document** — offering "University: OU" when no OU file is uploaded hands the user a guaranteed-empty filter. Global, not scoped to the current corpus selection (a scoped list would reshuffle the checkboxes on every tick).
- **The `__none__` sentinel is duplicated across the Django/dataplane boundary** rather than shared — the data plane can't import Django code.

### Data plane specifics
- **`dataplane/models/tables.py` must be kept in step with Django's migrations by hand.** It is a mirror, and drift is silent. `dataplane/tests/test_schema_drift.py` exists to catch it — and a new table must be registered in **all three** places there (import list, `SA_TABLES`, `parametrize`); one without the others defeats the guard. This has already bitten once: `Corpus.is_public` was added by migration `0011` and never mirrored.
- **KWIC's non-`center` sorts fetch the full match set** (capped at 10,000), hydrate, sort in Python, then slice — mirroring the Django view's own sort-before-paginate shape, just against indexed data. `center` stays fully SQL-paginated. An unrecognized `sort` value is a **422**, a deliberate divergence from `_sort_kwic`'s silent fallback.

### Operational
- **Performance.** The Django analysis pages re-parse XML for every selected document on every request, so cost scales with the number of selected files. The FastAPI path doesn't — it sums precomputed aggregates. This is the main reason the two surfaces are not equally fast on large corpora.
- **`ALLOWED_HOSTS`** defaults to `[]` — set it in `.env` before running with `DEBUG=False`. Django's test client adds `testserver` automatically, so tests need no override.
- **Tests.** Django: **184** tests via `python manage.py test main`, runnable on SQLite (`DB_ENGINE=django.db.backends.sqlite3 DB_NAME=:memory:`). Data plane: `python -m pytest dataplane/tests` needs a **real** Postgres (asyncpg has no SQLite equivalent) **whose `DB_NAME` contains "test"** — the fixture refuses otherwise, because it truncates `Document`/`Corpus` rows. Both need `CELERY_TASK_ALWAYS_EAGER=1`. Exact commands: `info/new_device.md` §11.
- **Multi-line `{# … #}` template comments do not work.** Django's comment token is single-line only, so a multi-line `{# … #}` renders into the page as literal text. Use `{% comment %} … {% endcomment %}`.

---

## 7. The Context Bar Pattern

`main/templates/main/components/context_bar.html` is included at the top of every analysis template. It holds the state that applies to every feature — **which corpora**, **original vs corrected**, and **the metadata facets** — and is what makes them survive navigation:

- It is a `method="get"` form with **no `action`**, so it submits back to whatever feature page it's currently on.
- It always carries the hidden `corpus_selection=1` marker. Unticking every box submits no `corpus` params at all, which is otherwise indistinguishable from "form not submitted"; without the marker a selection could never be cleared.
- Text mode is a pair of radios (`corrected=0|1`), not a checkbox: "original" is a real choice, not the absence of one. They auto-submit; corpus changes are applied with a button, because a reload per tick is slow and disorienting when selecting several.
- `views._resolve_selected_corpus_ids` writes the corpus result to `request.session['selected_corpus_ids']`, so every other feature picks it up with no URL params at all. Ids also travel in the query string when submitted, so feature URLs stay shareable.
- **The metadata facets live in the query string only — no session.** Corpus ids are in the session because repeating them in every URL would grow without bound; three short facets won't, and keeping them in the URL makes a filtered analysis shareable. `components/metadata_picker.html` renders them, and `_metadata_facet_context` supplies the available values.

**Parameters travel by tag, not by hand.** `main/templatetags/kuis.py` is what keeps several forms on one page from discarding each other's state:

- `{% hidden_params exclude="q,match,page" %}` re-emits the whole current query string as hidden inputs minus the named keys. A form changes its own parameters and preserves everyone else's — including parameters added later, which is why no form has a hardcoded list of fields to carry. **The one thing `exclude` must name is every metadata facet** when the real checkboxes are also present, or each value submits twice.
- `{% qs page=3 %}` rewrites the current query string for a link (empty value drops the key). Pagination, sort headers, "clear filter" and every export link use it.
- `{% sort_url 'count' 'desc' %}` and `{% aria_sort 'count' %}` drive `components/sort_header.html`: clicking the active column flips direction, a new column starts at its natural direction and returns to page 1.

**To add a new analysis feature to the Django surface**, follow the existing shape: `@login_required` → `_require_selection(request)` → `_get_selected_documents(request)` (this is where both the corpus selection *and* the metadata filter are already applied — do not re-apply either) → `_count_words(...)` or your own fold → `_rank_counter(request, counter, total)` for the table controls → merge `_analysis_context(request, documents, feature='…')` into the context → in the template, include `components/context_bar.html` and `components/analysis_nav.html` (add a tab), and delegate results to `components/frequency_results.html`. Add the `/export/` route with `_csv_response` and `_sorted_rows_for_export` so it exports like every other one.

**To add one to the data plane**, the shape is: an ABC in `repositories/base.py` → a Postgres implementation in `repositories/postgres/` (reuse `_ranked_query`/`_aggregate_query`/`_ranked_pagination` if the result is a ranked table) → a service → a response schema → a router that `Depends()` on `get_current_user` and `MetadataFilterParams` → register it in `dataplane/main.py` and add a `get_*_service` to `dependencies.py`. Resolve documents through `repositories/shared.py::resolve_document_ids` so corpus scoping and the metadata filter come along for free. If the feature needs a new table, mirror it in `models/tables.py` **and** register it in all three places in `tests/test_schema_drift.py`.

**UI conventions worth keeping.** Numbers are right-aligned and tabular; sort direction and text mode are carried by a glyph and `aria-sort`/`aria-current`, never by colour alone; loading, empty and error are three distinct states (`components/state.html`, the `.notice` variants, and `body.is-loading`), because "still working" and "nothing found" must never look the same.

**Theming.** Light and dark are an explicit reader choice, **not** `prefers-color-scheme` — the default is light, so the app looks the same on any machine until someone chooses otherwise. The light palette is the `:root` token block; dark redefines the same token names under `:root[data-theme="dark"]`, which means no component rule ever hardcodes a colour. The choice is stored in `localStorage` under `kuis-theme` and re-applied by a small inline script in `base.html`'s `<head>` **before** first paint, so a dark-theme reader never sees a flash of the light page; `kuis.js` only wires the switch and unhides it.

---

## 8. Migration / Handover Guide

> **Setting up a machine from zero is its own document: `info/new_device.md`.** It covers prerequisites, Postgres/Redis, both `.env` files, the reference-data loads, all four processes, the test commands and a troubleshooting table. This section is the summary of what a handover has to decide, not the step list.

### Must be configured manually
1. **Python 3.14.x + a fresh venv.** No `.python-version` pin exists in-repo; 3.14.3 locally, `python:3.14-slim` in Docker, `3.14` in CI. Never reuse a venv copied from another machine.
2. **Node 22.x** for `KUIS-FE` (Next.js 16 requires ≥20.9). Never reuse a copied `node_modules`.
3. **`.env` at the `KUIS` root** — copy `.env.example`, then fill `SECRET_KEY` and `JWT_SECRET` with two **different** freshly generated values (`python -c "import secrets; print(secrets.token_urlsafe(64))"`), the `DB_*` vars, and the `SEED_*` vars. Both secrets are **required** and read at settings-load time, so Django won't start without either even if you only want the server-rendered pages.
4. **`.env.local` at the `KUIS-FE` root** — the two backend base URLs only. No secret belongs in that repo; it holds tokens, never the signing key.
5. **PostgreSQL 16** — one role + one empty database matching your `.env`. Optionally a second database whose name contains "test", for the data plane suite.
6. **Redis 7** — not optional. The worker is the only writer of every derived table.
7. **Admin + test user** — `python manage.py seed`. **Required**: without an `is_staff` account nobody can upload files or create corpora, which means no analysis is possible at all; the non-admin account is what lets you exercise the RBAC boundary (§5) without a second manual signup. Creates the admin as `is_staff` **and** `is_superuser`, so it is also your only Super Admin. Idempotent; `--update` resets password/flags. `createsuperuser` still works as a one-off alternative for the admin only.
8. **Reference data, in this order** — `load_error_taxonomy` **before any upload** (annotations resolve against that table at extraction time), then `load_metadata_catalogue`. Both are idempotent and deliberately kept out of migration history, because taxonomy glosses and catalogue rows get corrected independently of the schema.
9. **Deployment infrastructure** — `docker/{django,fastapi,worker}.Dockerfile`, `docker-compose.yml` and `.github/workflows/{ci,deploy}.yml` exist. Note the compose stack publishes **no host ports** (an externally deployed `nginx-gateway` is the only public entry point) and needs `docker network create app-shared` once per machine. **KUIS-FE has no Docker or CI files yet.** Revisit `ALLOWED_HOSTS`, `EMAIL_BACKEND` and `STATIC_ROOT`/`MEDIA_ROOT` before deploying with `DEBUG=False`.

### Handled automatically
- Schema and the two data migrations (`0006` legacy corpus, `0008` WordType backfill) via `python manage.py migrate`.
- Content-hash dedup at upload time, on all six upload paths.
- Metadata linking on document creation (and in the other direction at import time).
- Tokenization, Tier-2 aggregates and error-annotation extraction — **by the worker, asynchronously**, and only for newly created documents. Not on later content edits.
- Dependency installation via `pip install -r requirements.txt` (one file, all three backend processes) and `npm install`.

### Outstanding / known gaps
- Migration `0014` is **not applied to `corpus_db`** and the metadata catalogue is **not imported there** — test environments only.
- Phase 8 (caching, retiring superseded Django views) is not started.
- `worker/tasks/maintenance.py` doesn't exist — `retokenize` stays a management command, and there is no automatic staleness check.
- `KUIS-FE/README.md`'s phase table is stale (describes dashboard pages as stubs). `docs/new-architecture.md` is the authority.
- No browser click-through verification exists for the taxonomy tri-state checkbox visuals, the summary tree's disclosure toggles, or the metadata picker's rendering inside `ContextBar` — all were verified by server-rendered output and tests, not by clicking.

---

## 9. Developer Quick Start

Full detail, with Windows commands and troubleshooting: **`info/new_device.md`**.

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
python manage.py seed                      # not optional — see §8.7
python manage.py load_error_taxonomy       # before any upload — see §8.8
python manage.py load_metadata_catalogue
```

Then **three terminals**, each with the venv activated:
```bash
python manage.py runserver                               # :8000  Django
uvicorn dataplane.main:app --reload --port 8001          # :8001  FastAPI
celery -A worker.celery_app worker --loglevel=info       #        worker
```
Health checks: `http://127.0.0.1:8000/` · `http://127.0.0.1:8001/health` · `http://127.0.0.1:8001/docs`.
On Windows add `--pool=solo` to the Celery command, or tasks never execute.

### Frontend (`KUIS-FE`, sibling directory)
```bash
cd ../KUIS-FE
cp .env.example .env.local      # must match the ports above
npm install
npm run dev                     # :3000
```

### First run-through
1. Open `http://127.0.0.1:8000/` and log in as the seeded admin (`SEED_ADMIN_EMAIL`; username or email both work).
2. **Corpora** → *Create New Corpus* → name it, set public/private, upload XML from `info/*.xml`. Watch the worker terminal: `index_document` must run, then the chained aggregate and error-annotation tasks. **If nothing appears there, Redis or the worker is down** — fix that first, or the document stays at 0 tokens and is invisible to every analysis feature.
3. **Analysis** → tick a corpus → Word Frequency / Collocations / N-grams / KWIC. This surface parses XML live, so it works even with the data plane down.
4. Open `http://localhost:3000`, log in with the same credentials, and run Word Frequency there. A result proves the whole chain: Next.js → Django (issues the token) → FastAPI (verifies it statelessly) → Postgres.
5. `/analysis/error-analytics` in the frontend — **only** there, no server-rendered equivalent. `info/KUIS2023FUA201-Eror.xml` carries real `<segment>` annotations, so Error Frequency, EPIC and the Summary tree should all be populated.
6. Try the **metadata filter** in the context bar on any analysis page, including a facet's "(no value)" option.
7. Log in as the seeded non-admin (`SEED_USER_EMAIL`) in a separate session to confirm the upload/create/edit/delete/assign affordances are hidden *and* blocked, private corpora 404, and `/users` + `/admins` are inaccessible (§5).

### Tests
```bash
# Django — 184 tests, SQLite, no Postgres needed
CELERY_TASK_ALWAYS_EAGER=1 DB_ENGINE=django.db.backends.sqlite3 DB_NAME=":memory:" \
  python manage.py test main

# Data plane — needs a real Postgres whose DB_NAME contains "test"
CELERY_TASK_ALWAYS_EAGER=1 DB_NAME=kuis_dataplane_test python manage.py migrate --noinput
CELERY_TASK_ALWAYS_EAGER=1 DB_NAME=kuis_dataplane_test python -m pytest dataplane/tests -v

# Frontend — no unit tests
cd ../KUIS-FE && npm run build && npm run lint
```

---

## Flagged uncertainties (do not treat as fact without verifying)

- **Migration `0014` and the metadata catalogue are not applied to `corpus_db`.** Everything described about `DocumentMetadata` and the metadata filter is verified in test environments only. The loading sequence is in `docs/metadata-catalogue-plan.md`.
- **A project-wide `retokenize` has never been run for real** on `corpus_db`. A dry-run showed nothing currently needs it, which is not the same as having done it.
- **No browser click-through verification** exists for the taxonomy tri-state checkbox visuals, the error-summary tree's disclosure toggles, or the metadata picker's rendering inside KUIS-FE's `ContextBar` — those were checked by server-rendered output and tests, not by clicking.
- **`DB_ENGINE=django.db.backends.sqlite3` works for the Django suite** (CI runs it that way) but is **not** a usable local dev database for the full stack: the data plane requires asyncpg/Postgres, with no SQLite path at all.
- **Whether uploaded files are expected to always follow the corpus XML schema, or plain prose is also legitimate** — the code supports both (the flat-tokenize fallback) but this has never been stated as an explicit product decision.
- **`KUIS-FE/README.md` contradicts reality** on feature status. Treat `docs/new-architecture.md` as the authority, not that file.
- **The six-upload-path count** (`main/views.py`'s `corpus_create`/`corpus_edit`/`upload_document`, `main/api_documents.py`, and `main/api_corpus.py`'s `CorpusListView`/`CorpusDetailView`) comes from `docs/content-hash-dedup.md`. If you add a seventh, it must go through `main/document_ingest.py::get_or_create_document` or it silently skips dedup *and* metadata linking.
