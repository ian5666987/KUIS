# KUIS — Feature List & Architecture Overview

> What exists and roughly how it fits together. Enough for a new developer to
> know what the system does, which service owns each part, and where to look.
> Deep detail on storage, indexing and querying is `docs/data-processing.md`;
> machine setup is `docs/ONBOARDING.md` and `info/new_device.md`; dated history
> is `docs/CHANGELOG.md`.

---

## 1. What KUIS is

A corpus-linguistics application for an Indonesian-language **learner corpus**.
Admins upload XML files (plain text plus `<segment>` error annotations carrying
corrections) and group them into named **corpora**. Registered users then run
reports across any multi-select of those corpora — word frequency, collocations,
n-grams, KWIC, and error analytics — each with an *original vs. corrected* text
toggle and each narrowable by a secondary per-document **metadata filter**
(university / year / grade / name code, plus the writer's gender and mother
tongue).

## 2. Architecture at a glance

```
KUIS/                                   KUIS-FE/
├── config/  main/     Django  :8000    └── Next.js 16 / React 19  :3000
├── dataplane/         FastAPI :8001
├── worker/            Celery  (Redis broker)
└── one PostgreSQL database, schema owned by Django's migrations
```

Four processes, two repositories, **two live user interfaces**.

| Service | Role | Key constraint |
|---|---|---|
| **Django** (`config/`, `main/`) | Back office + the auth/RBAC source of truth. Serves *two* surfaces: server-rendered pages (session cookies) and a JSON/JWT API under `/api/` (`main/api_*.py`) for KUIS-FE | Owns the schema. Both surfaces are live, so **every access rule is implemented twice** (§6) |
| **FastAPI** (`dataplane/`) | **Read-only** analysis data plane. Every analysis feature in KUIS-FE reads here, not from Django | Verifies Django-issued JWTs statelessly (shared `JWT_SECRET`, zero calls back to Django). SQLAlchemy **Core** table mirrors, no ORM, no Alembic. Layered `routers/ → services/ → repositories/` behind ABCs in `repositories/base.py` |
| **Celery worker** (`worker/`) | The **only writer** of derived tables: tokens, aggregates, error annotations | Bootstraps Django (`django.setup()`) so tasks reuse `main.models.build_tokens()`. **An upload with no worker running leaves the document untokenized and invisible to every analysis feature** |
| **Next.js** (`KUIS-FE`, sibling repo) | The frontend, talking to both backends directly | Its own structure is governed by `KUIS-FE/CLAUDE.md`. Its `README.md` phase table is stale |

Design goals worth knowing: FastAPI endpoints never depend on Postgres-specific
query logic, so a repository can be swapped (ClickHouse/OpenSearch) without
touching services, routers or the frontend — a swap that has already happened
once internally, when frequency/collocations/n-grams moved from raw tokens onto
precomputed aggregates with nothing above the repository changing. Django's
migrations are the single schema authority; FastAPI and the worker are schema
*consumers* and never issue migrations.

### Surface parity — the two UIs are not equal, in both directions

| | Server-rendered Django | KUIS-FE (Next.js) |
|---|---|---|
| Frequency / collocations / n-grams / KWIC (legacy + fast) | ✅ live XML parse or `Token` scan | ✅ via FastAPI, indexed |
| CSV export of each of the above | ✅ `/export/` routes | ✅ |
| Corpus CRUD, bulk assign, upload, public/private | ✅ | ✅ |
| Metadata secondary filter | ✅ all five facets | ✅ gender/mother-tongue controls **not built yet** |
| **Error analytics** (Error Frequency, EPIC, Summary) | ❌ none | ✅ only here |
| **User management** (Admins/Super Admins) | Django `/admin/` only | ✅ `/users`, `/admins` |

The server-rendered interface is a self-contained stylesheet
(`main/static/main/kuis.css`) plus one optional progressive-enhancement script
(`main/static/main/kuis.js`) — **no framework, no CDN, no Bootstrap**; every page
works with JavaScript disabled. Light/dark is an explicit reader choice stored in
`localStorage` (`kuis-theme`), re-applied by an inline script in `base.html`'s
`<head>` before first paint, *not* `prefers-color-scheme`.

---

## 3. Back office (Django)

Access column: **anyone** · **registered** (any logged-in active user) ·
**admin** (`is_staff`) · **super admin** (`is_superuser`) — see §6.

| Feature | Access | Description | Key files |
|---|---|---|---|
| **Public pages** (Home/About/Contact) | anyone | Static pages; Contact sends mail via `ContactForm` (console backend) | `main/views.py`, `main/forms.py`, `main/templates/main/{home,about,contact}.html` |
| **Registration** | anyone | `UserCreationForm` subclass + required email (rejected if another account uses it — email is a sign-in identifier); auto-logs in | `main/views.py::register`, `main/forms.py::RegisterForm` |
| **Login / Logout** | anyone | Custom `LoginView` accepting a username *or* the account's email (case-insensitive) via `UsernameOrEmailBackend`; stock POST-only `LogoutView` | `main/views.py::CustomLoginView`, `main/auth_backends.py` |
| **Dashboard / Profile** | registered | Minimal authenticated landing pages | `main/views.py` (`dashboard`, `profile`) |
| **Corpus list / detail** | registered | Browse corpora, files, token counts, overlaps. Private corpora filtered out for non-staff; management buttons render only for admins | `main/views.py` (`corpus_dashboard`, `corpus_detail`, `_annotated_corpora`) |
| **Create / edit / delete corpus** | **admin** | Name + description, public/private toggle, tick existing files and/or upload new XML inline. Deleting a corpus keeps its files | `main/views.py` (`corpus_create`, `corpus_edit`, `corpus_delete`), `main/forms.py::CorpusForm` |
| **Bulk assign** | **admin** | Many files → many corpora in one submit, from a modal | `main/views.py::corpus_assign`, `components/assign_modal.html` |
| **Document upload** | **admin** | Standalone single-file upload (paste text or a UTF-8 file). Content-hash deduped, then indexed asynchronously | `main/views.py::upload_document`, `main/document_ingest.py` |
| **Public / private corpora** | **admin** sets | `Corpus.is_public` (`default=True`). Private → staff only, and a private corpus requested by a non-staff user is a **404, not a 403** — a 403 would confirm it exists. Enforced independently on both surfaces | `main/models.py`, `main/views.py`, `main/api_corpus.py`, migration `0011` |
| **Django Admin** | **admin** | `Document`, `Corpus`, `ErrorTaxonomyNode`, `ErrorAnnotation`, `DocumentErrorFreq` registered. `UserProfile` and `DocumentMetadata` are **not** | `main/admin.py` |

---

## 4. Analysis features

Available on both surfaces unless noted. Every one of them is scoped by the
corpus multi-select **and** the metadata filter, and respects the
original/corrected text mode.

| Feature | Description | Endpoints / key files |
|---|---|---|
| **Analysis hub** | Corpus multi-select + scope statistics + links into every feature; selection remembered in the session (Django) / persisted client-side (KUIS-FE) | `main/views.py::analysis_home` |
| **Word Frequency** | Ranked word counts with per-million normalisation; filter (contains/starts/ends/exact), sort by word or count, paginate | Django `views.word_frequency`; `GET /api/v1/frequency` |
| **Collocations** | Adjacent word-pair counts, same table controls. Keeps its own interface despite being n=2 internally — collocations have their own product future (e.g. mutual-information scoring) | Django `views.collocations`; `GET /api/v1/collocations` |
| **N-grams** | n-length word tuples, `n` a 2–5 segmented control (`?n=`) | Django `views.ngrams`; `GET /api/v1/ngrams` |
| **KWIC (Legacy)** | Concordance search for a word **or phrase**, context window 2–10, sortable four ways (`center` = corpus order, default · `left` · `right` · `document`), each line attributed to its file | Django `views.kwic` (a live XML re-parse); `GET /api/v1/kwic?sort=` (indexed; an unrecognized value is a **422**, a deliberate divergence from the Django view's silent fallback) |
| **KWIC (Fast)** | Same concordance view for a **single word form**, answered from the index rather than re-parsing; no sort control | Django `views.kwic_search`; the same `/api/v1/kwic` with `sort=center` |
| **CSV export** | Django: every analysis has an `/export/` route re-running the same query with the same filter and sort, unpaginated. KUIS-FE builds its own export URLs | `main/views.py` (`*_export_csv`, `_csv_response`, `_sorted_rows_for_export`) |

### Error analytics — KUIS-FE only

`info/error.xml` defines ~90 Indonesian error codes (root `eror` → 4 categories →
optional subcategories → leaf codes, e.g. `eror;gramatikal;frasa-nomina;nomafx`).
Real documents already carried these as `<segment features="...">` annotations,
previously never parsed or stored.

| Feature | Description | Endpoint |
|---|---|---|
| **Error Frequency** | Word-frequency's shape but for erroneous *phrases* — `{phrase, frequency}`, filtered by any set of taxonomy codes | `GET /api/v1/error-frequency` |
| **EPIC** (Error Phrase In Context) | KWIC's shape but centered on annotated errors: left/keyword/right context plus the error code and the correction text | `GET /api/v1/epic` |
| **Error Summary** | Expandable tree over the *whole* taxonomy; each row carries its count, its share of all errors, and its share of its parent | `GET /api/v1/error-summary` (`by_node` + the original `by_category`) |
| **Taxonomy picker** | Tri-state cascading checkbox tree over the ~90 codes. Checking a category cascades to every leaf beneath it; checking nodes from several branches **unions** their results | `GET /api/v1/taxonomy`; `KUIS-FE/lib/taxonomy.ts`, `components/features/analysis/TaxonomyPicker.tsx` |

Code: `dataplane/{routers,services,schemas}/{error_analytics,taxonomy}.py`,
`dataplane/repositories/postgres/{_error_query,error_analytics_repository,taxonomy_repository}.py`,
`worker/tasks/error_annotations.py`, `main/management/commands/load_error_taxonomy.py`.
Frontend: `components/features/analysis/{ErrorAnalytics,EpicTable,ErrorSummaryTable}.tsx`,
`hooks/fastapi/{useTaxonomy,useErrorAnalytics}.ts`, route `/analysis/error-analytics`.

**One filter model, one mechanism.** Both search features share a flat,
OR-combined list of taxonomy codes at *any* depth. A leaf's `path` always ends
with its own code, so `concat(';', path, ';') LIKE '%;code;%'` against the tiny
`ErrorTaxonomyNode` table matches both a direct leaf hit and every descendant of
a checked category — no separate exact-match branch. The frontend collapses a
fully-selected subtree back to its ancestor code before sending
(`lib/taxonomy.ts::compressSelection`), a size optimisation only. *(An earlier
design AND-combined a separate category axis for drill-down; dropped once the
real tree picker existed — AND-across-branches isn't how anyone expects a
checkbox tree to behave.)*

**Two deliberate quirks.** The summary's `count` is **already subtree-inclusive**
— the frontend never sums descendants, it only divides to get a percentage; the
roll-up happens in Python (`_error_query.py::compute_error_summary`), not a
recursive CTE, because `path` already spells out every ancestor's code. And the
summary and the two search features **disagree by design**:
`resolve_leaf_node_ids` filters `is_leaf = TRUE`, but a `DocumentErrorFreq` row
can legitimately point at a non-leaf node (`features="eror;leksikal"` is real
data), so a non-leaf-resolved annotation is counted by the Summary and never
matched by Error Frequency or EPIC. `self_count` is what makes that visible in
the UI (`(N not sub-classified)`) instead of letting the arithmetic look broken.

### Metadata secondary filter

University / Year / Grade / Gender / Mother tongue (checkbox facets) + name code
(prefix). **OR within a facet, AND across facets.** Each facet has a
**"(no value)"** option (`__none__`) matching both a blank field *and* a document
the catalogue doesn't cover. Composes with corpus selection on **every** analysis
feature, on both surfaces, and all five Django `/export/` routes inherited it for
free. Endpoint `GET /api/v1/metadata-facets`; see `docs/data-processing.md` for
the catalogue, the matching rule and the filter's internals.

---

## 5. User management (KUIS-FE + `/api/`, no server-rendered equivalent)

Before this existed, the only way to manage accounts was Django's `/admin/` or
the one-time `seed` command, and `is_superuser` — already set by `seed` — was
never read anywhere.

| Feature | Access | Description |
|---|---|---|
| **Users tier** — list/create/edit/block/unblock/soft-delete regular users | **admin** | `/api/users/…`, pages at `KUIS-FE/app/(dashboard)/users/`. Queryset is `is_staff=False`, excluding super admins and soft-deleted rows |
| **Admins tier** — the same five actions over admin accounts | **super admin** | `/api/admins/…`, pages at `KUIS-FE/app/(dashboard)/admins/`. Queryset is `is_staff=True, is_superuser=False` |
| **Block / unblock** | per tier | Sets `is_active` plus `profile.blocked_at/by`, and **blacklists outstanding refresh tokens** |
| **Soft delete** | per tier | Deactivate, anonymize PII, record `deleted_at/by`, exclude from every endpoint afterwards. Terminal — no undelete |

| Method | Path | Permission |
|---|---|---|
| GET/POST | `/api/users/?search=&status=&page=&per_page=` | `ADMIN_PERMISSIONS` (`is_staff`) |
| GET/PATCH/DELETE | `/api/users/<id>/` | `ADMIN_PERMISSIONS` |
| POST | `/api/users/<id>/block/`, `/unblock/` | `ADMIN_PERMISSIONS` |
| GET/POST | `/api/admins/?search=&status=&page=&per_page=` | `SUPERADMIN_PERMISSIONS` (`is_superuser`) |
| GET/PATCH/DELETE | `/api/admins/<id>/` | `SUPERADMIN_PERMISSIONS` |
| POST | `/api/admins/<id>/block/`, `/unblock/` | `SUPERADMIN_PERMISSIONS` |

**The decisions that matter here:**

- **Soft delete, never a hard DB delete.** `Document.user` is `CASCADE`, so
  hard-deleting a user would take every document they uploaded *and* all its
  derived analytics rows.
- **Blacklist refresh tokens on block and delete.** `JWTAuthentication.get_user()`
  re-checks `is_active` on every request so a live access token dies immediately,
  but `TokenRefreshView` does **not** — without the blacklist a blocked user could
  still mint a fresh access token from a pre-block refresh token.
- **Super Admins are not manageable through this feature at all.** Both tiers
  unconditionally exclude `is_superuser=True`, so no request can view, edit, block
  or delete one via either endpoint, regardless of who is asking. They stay
  seed/CLI-provisioned.
- **`is_staff`/`is_superuser` are never read from a request body** — they are
  server-set from *which endpoint was called* (`/api/users/` forces both `False`;
  `/api/admins/` forces `is_staff=True, is_superuser=False`). This is what stops a
  crafted `{"is_staff": true}` payload from self-elevating a user.
- **`is_active` is excluded from the general write serializer**, so
  `blocked_at`/`blocked_by` can never drift out of sync with a bare PATCH.
- **A `UserProfile` sidecar, not a custom `AUTH_USER_MODEL`.** Swapping that 12
  migrations in — with `Document.user`, `Corpus.created_by` and `token_blacklist`
  already pointing at `auth.User` — would be a large, high-risk change for four
  columns. Rows are created lazily via `get_or_create`, never backfilled; a
  profile-less account reads as "active, never blocked."
- **One shared CRUD implementation, two thin tiers.** A private
  `_AccountQuerysetMixin` scopes every view by `is_staff_tier`; the eight views are
  two-line subclasses of shared private base classes.
- **Self-action guard kept despite being structurally unreachable today.** Given
  the tier exclusions, an acting user's own row can never appear in the tier they
  manage; `assert_not_targeting_self` is still called in the block/delete paths as
  defense-in-depth against a future tier-rule change reopening a lockout vector.

Code: `main/permissions.py`, `main/api_users.py`, wired in `main/api_urls.py`.
`main/api_auth.py` adds `is_superuser` to the JWT alongside `is_staff` — frontend
cosmetics only. `dataplane/` is untouched by this feature.

**Deferred:** undelete/reactivate · an audit view of deleted accounts ·
Admin-manages-Admin or SuperAdmin-manages-SuperAdmin (out of scope per confirmed
requirements) · bulk actions · welcome emails with a set-password link ·
`drf-spectacular` codegen for these endpoints · a global
`DEFAULT_PAGINATION_CLASS` (pagination is scoped to `AccountPagination` for these
two list views only, leaving Corpus's unpaginated list untouched).

---

## 6. Access control (RBAC)

Four tiers, driven entirely by Django's built-in `User.is_staff` and
`User.is_superuser`. **There is no role or permission table** — an admin is a
user with `is_staff=True`, a super admin one with `is_superuser=True`.

| Tier | Who | Can do |
|---|---|---|
| **Anonymous** | not logged in | Home, About, Contact, Register, Login. Every feature URL redirects to `/accounts/login/?next=…` |
| **Registered** | any logged-in active user | All analysis features, the hub, **public** corpus list/detail (read-only), dashboard, profile |
| **Admin** | `is_staff=True` | The above **plus** private corpora, XML upload, corpus create/edit/delete/assign, the public/private toggle, Django `/admin/`, and the **Users** tier |
| **Super Admin** | `is_superuser=True` | The above **plus** the **Admins** tier. Seed/CLI-provisioned only |

`is_superuser` gates exactly one thing — the Admins tier. **No analysis endpoint
has a staff gate at all.**

**Enforced three times, independently on purpose** — all three surfaces are live
simultaneously, so none can rely on another to filter:

1. **Server-rendered pages** — `main/views.py::staff_required`. **Decorator order
   matters**: always `@login_required` *above* `@staff_required`, so an anonymous
   visitor gets the login page while a signed-in non-admin gets a plain 403
   instead of being bounced to a login form they are already past. Admin-only
   views: `upload_document`, `corpus_assign`, `corpus_create`, `corpus_edit`,
   `corpus_delete`. Templates gate affordances with `{% if user.is_staff %}`, but
   the decorators are the actual boundary.
2. **Django JSON API** — `main/api_auth.py` puts `is_staff`/`is_superuser` on the
   JWT as claims; DRF views gate with the shared constants from
   `main/permissions.py` (`ADMIN_PERMISSIONS`, `SUPERADMIN_PERMISSIONS`).
   `main/api_corpus.py` imports these rather than defining its own copy.
3. **FastAPI data plane** — `dataplane/dependencies.py::get_current_user` decodes
   the token statelessly and never calls back to Django; `require_staff` checks the
   claim. Currently **no analysis router uses `require_staff`** — every endpoint is
   authenticated-only.

**Account state** (`UserProfile`): **active** (`is_active=True`,
`deleted_at IS NULL`) · **blocked** (`is_active=False`, `deleted_at IS NULL`,
reversible) · **deleted** (`is_active=False`, `deleted_at IS NOT NULL`, terminal
— excluded from every queryset, so the id 404s afterwards).

`python manage.py seed` gets you a super admin and a non-admin account for
exercising the tiers against all three surfaces without hand-editing users.

---

## 7. Management commands (CLI)

| Command | Purpose |
|---|---|
| `seed [--update]` | Bootstraps one super admin (`is_staff`+`is_superuser`) and one non-admin user from the `SEED_*` vars. Idempotent. **Required on a fresh database** — without an `is_staff` account nobody can upload or create corpora, so no analysis is possible |
| `load_error_taxonomy` | Loads the ~90-code taxonomy from `info/error.xml` into `ErrorTaxonomyNode`. Replaces the whole table each run. **Run before uploading documents** — annotations resolve against this table at extraction time |
| `load_metadata_catalogue [--file X.csv] [--dry-run] [--relink]` | Loads/upserts `metadata/*.csv` (1,557 entries from 1,559 rows) into `DocumentMetadata` and links each row to its `Document`. Idempotent; `--relink` re-resolves FKs without reading any CSV |
| `load_author_metadata [--file X.csv] [--dry-run] [--relink]` | Loads/upserts `metadata/authors/*.csv` (235 authors + 226 mother tongues from 437 rows) into `Author`/`AuthorMotherTongue`, linking on `(university, name_code)`. **Exits non-zero when the source data has an error** — by design; run `--dry-run` first and read the report |
| `retokenize [--dry-run]` | Rebuilds all `Token` rows from current `Document.content` **and** refreshes the Tier-2 aggregates. Run after changing tokenization logic or editing a document's content |
| `backfill_tier2 [--all]` | Computes `DocumentWordFreq`/`DocumentNgram` for documents that have tokens but no aggregates. Does not rebuild tokens |
| `backfill_error_annotations [--all]` | Extracts `ErrorAnnotation`/`DocumentErrorFreq` for documents uploaded before the taxonomy was loaded. Does not rebuild tokens |
| `backfill_content_hash` | Recomputes `content_hash` with the canonicalized formula. Only relevant to databases predating migration `0010`; reports collision groups rather than merging them |

---

## 8. Codebase map

```
config/
  settings.py   — all Django config: DB, JWT, CORS, seed vars
  urls.py       — root routing: admin/, main.urls, login/logout, api/
  wsgi.py, asgi.py — standard, unmodified

main/                          # Django app — back office + both auth surfaces
  models.py            — Document, Corpus, WordType, Token, DocumentWordFreq,
                         DocumentNgram, ErrorTaxonomyNode, ErrorAnnotation,
                         DocumentErrorFreq, UserProfile, DocumentMetadata,
                         Author, AuthorMotherTongue; build_tokens()
  views.py             — every server-rendered view
  urls.py              — page routing, split into /corpus/ and /analysis/
  api_urls.py          — every /api/ route, in one place
  api_auth.py          — KUISTokenObtainPairView: JWT with is_staff +
                         is_superuser claims
  api_account.py       — RegisterView, ContactView, ProfileView
  api_corpus.py        — CorpusList/Detail/Assign (public/private enforced)
  api_documents.py     — DocumentListView (?has_metadata=), DocumentUploadView
  api_users.py         — the two account tiers
  permissions.py       — IsSuperAdminUser, ADMIN_PERMISSIONS,
                         SUPERADMIN_PERMISSIONS, assert_not_targeting_self
  document_ingest.py   — the ONE definition of the dedup formula
  metadata_catalogue.py— match keys, the catalogue importer, the filter
                         vocabulary (FACET_FIELDS, UNSET, facet_values)
  author_metadata.py   — author CSV parsing, normalisation, linking
  corpus_parsing.py    — the XML tokenizer: parse_document,
                         extract_word_streams, extract_error_annotations,
                         extract_textfile_name, looks_like_structured_xml
  auth_backends.py     — UsernameOrEmailBackend
  forms.py, admin.py, apps.py
  tests.py             — 229 tests
  templatetags/kuis.py — hidden_params / qs / sort_url / aria_sort tags,
                         per_million + bar_width filters (§9)
  static/main/kuis.css — the whole interface: design tokens, then components
  static/main/kuis.js  — optional enhancements only; nothing depends on it
  management/commands/ — see §7
  migrations/          — 0001-0016 (0006 and 0008 are data migrations)
  templates/
    main/base.html               — layout, nav, messages, auth conditionals
    main/components/             — the shared UI vocabulary (§9)
      context_bar.html           — corpus selection + text mode + metadata
      corpus_picker.html, metadata_picker.html, assign_modal.html,
      analysis_nav.html, frequency_results.html, concordance_results.html,
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
                         NOT ORM models. Drift here is silent, which is what
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
  tests/               — 171 tests; needs a real Postgres (asyncpg)

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

metadata/              — metadata_2023.csv, metadata_2024.csv (the file
                         catalogue) + authors/{KUIS,OU,TUFS}.csv (person-level)
info/                  — NOT code: error.xml (the taxonomy source), two sample
                         corpus XML files, a proposal PDF, informal notes,
                         new_device.md and quickstart.md
docs/                  — ONBOARDING.md, this file, data-processing.md,
                         CHANGELOG.md
docker/                — django / fastapi / worker Dockerfiles
```

### Representative flows

**Word Frequency, server-rendered.** `GET /analysis/frequency/` →
`views.word_frequency` → `_require_selection` (bounces to the hub if nothing is
selected) → `_get_selected_documents(request)` resolves session corpus ids **and
the metadata facet params** to a `.distinct()` queryset → `_count_words()` parses
each document into one word list per document and folds them into a `Counter` →
`_rank_counter()` applies the filter, sort and pagination shared by all three
count analyses → renders the template, which includes `context_bar.html` and
delegates results to `frequency_results.html`. The `/export/` route runs the same
pipeline unpaginated.

**Word Frequency, KUIS-FE.** `/analysis/frequency` →
`hooks/fastapi/useRankedSearch.ts` → `GET /api/v1/frequency` with the bearer token
→ `routers/frequency.py` (`Depends(get_current_user)`,
`Depends(MetadataFilterParams)`) → `FrequencyService` →
`PostgresFrequencyRepository` → `repositories/shared.py::resolve_document_ids`
turns corpus ids + the metadata filter into a document id list → `_ranked_query`
sums the **precomputed `DocumentWordFreq` aggregate** over those ids. No XML is
parsed at request time on this path.

**Upload.** `POST` on any upload path →
`document_ingest.get_or_create_document()` canonicalizes, hashes, and
`get_or_create`s on `content_hash` → **if created**, `index_document.delay(id)`
and `link_catalogue_entry()` → the worker tokenizes, writes the hashes, then
chains the two aggregate tasks and the error-annotation task. **If reused**, none
of that runs and the existing document's title/uploader are left alone. Detail:
`docs/data-processing.md`.

---

## 9. The context bar pattern (and how to add a feature)

`main/templates/main/components/context_bar.html` is included at the top of every
analysis template. It holds the state that applies to every feature — **which
corpora**, **original vs corrected**, and **the metadata facets** — and is what
makes them survive navigation:

- A `method="get"` form with **no `action`**, so it submits back to whatever
  feature page it is on.
- It always carries the hidden `corpus_selection=1` marker. Unticking every box
  submits no `corpus` params at all, otherwise indistinguishable from "form not
  submitted" — without the marker a selection could never be cleared.
- Text mode is a pair of radios (`corrected=0|1`), not a checkbox: "original" is a
  real choice, not the absence of one. They auto-submit; corpus changes are applied
  with a button, because a reload per tick is slow and disorienting.
- `views._resolve_selected_corpus_ids` writes the corpus result to
  `request.session['selected_corpus_ids']`, so every other feature picks it up with
  no URL params. Ids also travel in the query string when submitted, so feature
  URLs stay shareable.
- **The metadata facets live in the query string only — no session.** Corpus ids are
  in the session because repeating them in every URL would grow without bound;
  five short facets won't, and keeping them in the URL makes a filtered analysis
  shareable.

**Parameters travel by tag, not by hand** (`main/templatetags/kuis.py`) — this is
what keeps several forms on one page from discarding each other's state:

- `{% hidden_params exclude="q,match,page" %}` re-emits the whole current query
  string as hidden inputs minus the named keys, so a form changes its own
  parameters and preserves everyone else's — including ones added later, which is
  why no form has a hardcoded field list. **`exclude` must name every metadata
  facet** where the real checkboxes are also present, or each value submits twice.
- `{% qs page=3 %}` rewrites the current query string for a link (an empty value
  drops the key). Pagination, sort headers, "clear filter" and every export link
  use it.
- `{% sort_url 'count' 'desc' %}` and `{% aria_sort 'count' %}` drive
  `sort_header.html`: clicking the active column flips direction, a new column
  starts at its natural direction and returns to page 1.

**To add an analysis feature to the Django surface**, follow the existing shape:
`@login_required` → `_require_selection(request)` → `_get_selected_documents(request)`
(**both** the corpus selection and the metadata filter are already applied here —
do not re-apply either) → `_count_words(...)` or your own fold →
`_rank_counter(request, counter, total)` for the table controls → merge
`_analysis_context(request, documents, feature='…')` into the context → in the
template include `context_bar.html` and `analysis_nav.html` (add a tab) and
delegate results to `frequency_results.html`. Add the `/export/` route with
`_csv_response` and `_sorted_rows_for_export` so it exports like every other one.

**To add one to the data plane**: an ABC in `repositories/base.py` → a Postgres
implementation in `repositories/postgres/` (reuse
`_ranked_query`/`_aggregate_query`/`_ranked_pagination` if the result is a ranked
table) → a service → a response schema → a router that `Depends()` on
`get_current_user` and `MetadataFilterParams` → register it in `dataplane/main.py`
and add a `get_*_service` to `dependencies.py`. Resolve documents through
`repositories/shared.py::resolve_document_ids` so corpus scoping and the metadata
filter come along for free. A new table must be mirrored in `models/tables.py`
**and** registered in all three places in `tests/test_schema_drift.py`.

**UI conventions worth keeping.** Numbers are right-aligned and tabular; sort
direction and text mode are carried by a glyph and `aria-sort`/`aria-current`,
never by colour alone; loading, empty and error are three distinct states
(`state.html`, the `.notice` variants, `body.is-loading`), because "still working"
and "nothing found" must never look the same. No component rule hardcodes a
colour — dark mode redefines the same token names under `:root[data-theme="dark"]`.

**One template gotcha:** multi-line `{# … #}` comments do not work. Django's
comment token is single-line only, so a multi-line one renders into the page as
literal text. Use `{% comment %} … {% endcomment %}`.

---

## 10. Scoping rules every feature inherits

- **A document in no corpus is invisible to every analysis feature.** By design —
  analysis is corpus-scoped — which is exactly why migration `0006` exists.
  `corpus_dashboard` shows an "unassigned files" warning to admins so new orphans
  get noticed.
- **Corpus overlap is deduped via `.distinct()`.** Without it the M2M join returns
  a document once per matching corpus and counts it twice. Verified: selecting A∪B
  where B⊂A yields byte-identical output to selecting A alone.
- **Never flatten word lists across documents.** `_get_word_lists` returns *one
  list per document* precisely so collocation/n-gram/KWIC windows don't straddle a
  document boundary and invent pairs that exist in no real text. Any new
  sequence-based feature must fold per-document the same way.
- **Stale session corpus ids are pruned** in `_resolve_selected_corpus_ids` for
  both submitted and remembered ids — a stale browser tab can post a corpus id
  that has since been deleted.
- **Deleting a corpus never deletes its documents** — only the corpus row and its
  M2M links.
