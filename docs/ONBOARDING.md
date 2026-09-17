# KUIS — Developer Onboarding Document

> Technical onboarding reference for the existing Django application, based on a full review of the codebase as of this writing. Reflects the codebase as-is — no redesign or new architecture proposed.

---

## 1. Project Overview

**KUIS** is a Django 6.0.4 web app for analyzing an Indonesian-language learner corpus. Users upload documents containing XML-annotated text (plain text plus `<segment>` error annotations with corrections), then run corpus-linguistics reports against them: word frequency, collocations, n-grams, and KWIC (keyword-in-context) search — each with an "original vs. corrected" text toggle.

- **Architecture**: classic server-rendered Django monolith. No REST API, no JS framework/SPA — Bootstrap 5.3 (CSS only, via CDN) + Django template forms with `onchange="this.form.submit()"` auto-submit patterns for filters.
- **Django structure**: one project package `config/` (settings/urls/wsgi/asgi) + one app `main/` (all models, views, forms, admin, templates, the XML parser, migrations, one management command). `info/` at repo root is **not code** — it holds a proposal PDF, two sample corpus XML files, and informal notes (not a README).
- **Auth**: Django's built-in `auth.User` (no custom user model). Corpus features and account pages sit behind `@login_required`; a few public pages (Home/About/Contact/Register/Login) do not.
- **Database**: PostgreSQL via `psycopg2-binary`, configured entirely through env vars (`django-environ`).
- **No deployment tooling exists yet**: no README, no Dockerfile/Procfile, no CI/CD. This document is effectively the first onboarding reference for the project.

---

## 2. Feature Inventory

| Feature | Description | Key files |
|---|---|---|
| **Public pages** (Home/About/Contact) | Static marketing pages; Contact sends mail via `ContactForm` | `main/views.py:34-94` (`home`, `about`, `contact`), `main/forms.py` `ContactForm`, `main/templates/main/{home,about,contact}.html` |
| **Registration** | Signup via `UserCreationForm` subclass + required email; auto-logs in and redirects to dashboard | `main/views.py:52-62` (`register`), `main/forms.py` `RegisterForm`, `main/templates/registration/register.html` |
| **Login / Logout** | Custom `LoginView` subclass (Bootstrap-styled form); stock `LogoutView` (POST-only, via nav form) | `main/views.py:105-116` (`CustomLoginView`), `config/urls.py:32-33`, `main/templates/registration/login.html` |
| **Dashboard / Profile** | Minimal authenticated landing pages | `main/views.py:44,48` (`dashboard`, `profile`), `main/templates/main/{dashboard,profile}.html` |
| **Corpus Dashboard** | Lists all `Document`s as cards with links into each report | `main/views.py:119-123` (`corpus_dashboard`), `main/templates/main/corpus_dashboard.html` |
| **Document Upload** | Paste text or upload a `.txt` file (UTF-8 only); `Document.save()` triggers async-like token indexing via signal | `main/views.py:188-216` (`upload_document`), `main/forms.py` `DocumentForm`, `main/templates/main/upload_document.html` |
| **Word Frequency** | Top-20 word counts, original/corrected toggle | `main/views.py:134-148` (`word_frequency`), `main/corpus_parsing.py` `extract_word_streams`, `main/templates/main/word_frequency.html` |
| **Collocations** | Top-20 adjacent word-pair counts | `main/views.py:150-166` (`collocations`), same parser, `main/templates/main/collocations.html` |
| **N-grams** | Top-20 n-length word-tuple counts (n hardcoded to 3, not exposed in URL) | `main/views.py:168-185` (`ngrams`), `main/templates/main/ngrams.html` |
| **KWIC (Legacy)** | Per-document keyword-in-context search, sortable, paginated, live XML re-parse | `main/views.py:219-267` (`kwic_legacy`), `main/templates/main/kwic_legacy.html` |
| **KWIC Export CSV** | Same query as KWIC Legacy, downloads CSV | `main/views.py:366-401` (`kwic_export_csv`) |
| **KWIC (Fast/Search)** | Cross-document keyword search backed by the indexed `Token` table (DB query, not live parse) | `main/views.py:324-363` (`kwic_search`), `main/models.py` `Token`, `main/templates/main/kwic_search.html` |
| **Django Admin** | Only `Document` registered (default `ModelAdmin`) | `main/admin.py` |
| **`retokenize` management command** | Rebuilds all `Token` rows from current `Document.content` using the XML-aware tokenizer; use after changing tokenization logic or editing a document's content post-creation | `main/management/commands/retokenize.py` |

---

## 3. Codebase / Functionality Map

```
config/
  settings.py   — all Django config (see §4)
  urls.py       — root routing: admin/, main.urls, login/logout
  wsgi.py, asgi.py — standard, unmodified

main/
  models.py           — Document, Token models; build_tokens(); post_save signal
  views.py             — all view functions/classes (see §2 table)
  urls.py               — app routing, maps to views.py
  forms.py              — RegisterForm, ContactForm, DocumentForm
  admin.py              — registers Document only
  apps.py               — standard AppConfig, no hooks
  corpus_parsing.py    — XML tokenizer: extract_word_streams()
  tests.py              — empty stub, no tests exist anywhere in the repo
  management/commands/retokenize.py
  migrations/           — 0001-0004, standard schema migrations
  templates/
    main/base.html                — layout, nav, login-state conditionals
    main/{home,about,contact}.html
    main/{dashboard,profile}.html
    main/corpus_dashboard.html
    main/upload_document.html
    main/word_frequency.html, collocations.html, ngrams.html
    main/kwic_legacy.html, kwic_search.html
    main/kwic.html                — orphaned, see §5
    registration/{register,login}.html
```

**Representative flow — Word Frequency:**
`GET /corpus/frequency/<doc_id>/` (`main/urls.py:24`) → `views.word_frequency` (`main/views.py:134`) → `Document.objects.get(id=doc_id)` → `corpus_parsing.extract_word_streams(doc.content)` (parses the XML, splits into original/corrected word lists) → `_get_words` picks a stream based on `?corrected=1` → `collections.Counter` → `.most_common(20)` → renders `main/word_frequency.html`.

**Representative flow — Fast KWIC:**
`GET /corpus/kwic/search/?word=...` → `views.kwic_search` (`main/views.py:324`) → queries the pre-built `Token` table directly (`Token.objects.filter(word=..., mode=...)`) rather than re-parsing XML → renders `main/kwic_search.html`. The `Token` table is populated once, at `Document` creation time, by the `post_save` signal `create_tokens` (`main/models.py:72-81`) calling `build_tokens()` (`main/models.py:53-68`), which itself calls `extract_word_streams`.

---

## 4. Data & Architecture

### Models (`main/models.py`)
- **`Document`**: `title`, `content` (raw XML/text), `uploaded_at`, `created_at`, `user` (FK→`User`, nullable), `token_count`, `token_count_corrected`. Index on `(user, uploaded_at)`.
- **`Token`**: `document` (FK→`Document`, `related_name='tokens'`), `word`, `position`, `mode` (`original`|`corrected`). Indexes on `word` and `(document, mode, position)`. One row per token per mode per document — denormalized for fast KWIC lookups.

### Corpus XML format (parsed by `corpus_parsing.extract_word_streams`)
```xml
<document>
  <header><textfile>...</textfile><lang>indonesian</lang></header>
  <body>
    Plain text ... <segment id='104' features='eror;leksikal;kata;ktinf' Correction='tetapi'>tapi</segment> ...
  </body>
</document>
```
- `<segment>` marks an annotated error; `features` is a **semicolon-separated tag path** (`eror;<category>;<subcategory>;<leaf-code>`), depth varies by error type, and no fixed taxonomy is documented anywhere in the repo (only inferable from the two sample files in `info/`).
- The parser falls back to flat (non-XML-aware) tokenization of the raw content, identical for both streams, if content isn't parseable XML or has no `<body>` — silent degradation, no error shown to the user.
- Segments can **nest** (an error segment inside another); `_walk_body` (`main/corpus_parsing.py:18-34`) only walks direct children of `<body>` — see §5 for the resulting caveat.

### Authentication / Authorization
- Stock Django `auth.User`, session-based auth, `django.contrib.auth.urls` explicitly disabled in favor of custom login/logout routes only (**no password reset/change flow exists**).
- `LOGIN_REDIRECT_URL='dashboard'`, `LOGOUT_REDIRECT_URL='home'` (`config/settings.py:130-131`).
- `@login_required` is applied inconsistently — see the audit table in §5.

### External services / background jobs
- **None real.** `EMAIL_BACKEND` is the console backend (`config/settings.py:135`) — Contact form "sends" mail only to the dev console, not a real inbox. No Celery/RQ/cron, no external APIs, no caching layer.

### Configuration (`config/settings.py`, via `django-environ` reading `.env`)
| Env var | Feeds | Default |
|---|---|---|
| `SECRET_KEY` | Django `SECRET_KEY` | none (required) |
| `DEBUG` | `DEBUG` (bool) | `False` |
| `DB_ENGINE` | `DATABASES.default.ENGINE` | `django.db.backends.postgresql` |
| `DB_NAME` | `DATABASES.default.NAME` | none (required) |
| `DB_USER` | `DATABASES.default.USER` | none (required) |
| `DB_PASSWORD` | `DATABASES.default.PASSWORD` | none (required) |
| `DB_HOST` | `DATABASES.default.HOST` | `localhost` |
| `DB_PORT` | `DATABASES.default.PORT` | `5432` |

`ALLOWED_HOSTS = []` is **hardcoded** (not env-driven) — see §5. No `STATIC_ROOT`/`MEDIA_ROOT` configured (no static files exist yet in the repo; Bootstrap is CDN-only).

### Dependencies (`requirements.txt`)
`Django==6.0.4`, `django-environ==0.11.2`, `psycopg2-binary==2.9.12`, plus transitive pins `asgiref==3.11.1`, `sqlparse==0.5.5`, `tzdata==2026.1`. No Pipfile/pyproject.toml.

---

## 5. Edge Cases & Technical Considerations

- **`Token` rows go stale on content edits.** They're only (re)built by the `post_save` signal on `Document` **creation** (`main/models.py:72-81`) — if a document's `content` is ever updated afterward, `Token`/fast-KWIC data silently drifts out of sync with the real content. Run `python manage.py retokenize` after any bulk content edit or tokenizer-logic change.
- **`kwic_search` has no `@login_required`** (`main/views.py:324`), unlike every other corpus view — it's reachable anonymously at `/corpus/kwic/search/` even though it's only linked to from behind-login pages. Inconsistent with the rest of the access model; worth a deliberate decision (add the decorator, or confirm it's intentionally public).
- **`kwic_token` view + `kwic.html` template are dead/broken.** `kwic_token` (`main/views.py:270-322`) has `@login_required` but **no URL route** anywhere — unreachable. Its template `main/templates/main/kwic.html` references `{% url 'kwic' doc.id %}`, a URL name that doesn't exist — would raise `NoReverseMatch` immediately if ever wired up or rendered. Fix or remove together.
- **Duplicate `contact` view definition.** `main/views.py` defines `def contact(request)` twice (line 40, a stub; line 65, the real `ContactForm`-based one). Python silently keeps only the second — the first is dead code but harmless since `urls.py` resolves to the final definition. Clean up to avoid confusion.
- **`custom_login` is unused dead code** (`main/views.py:96-103`) — duplicates `CustomLoginView`, never routed in `urls.py`.
- **`Correction` vs `correction` attribute casing.** Sample corpus files use both `Correction='...'` and lowercase `correction='...'` inconsistently. `extract_word_streams`/`_walk_body` only reads `elem.get('Correction', ...)` (capital C) — segments using the lowercase attribute silently contribute **nothing** to the "corrected" word stream. Worth normalizing/checking both casings if this recurs in real uploads.
- **Nested `<segment>` elements are only partially walked.** `_walk_body` iterates direct children of `<body>` only; for a segment nested inside another segment, the outer segment's `.text`/`Correction` are read, but the inner nested segment isn't independently visited by this function (word-stream tokenization, not the same traversal a future error-analysis feature would need — that would require `body.iter('segment')` instead).
- **Malformed/non-XML content degrades silently.** `extract_word_streams` catches `ET.ParseError` and falls back to flat regex tokenization (same words for both original/corrected streams) — no user-facing warning that a document isn't being parsed as structured corpus XML.
- **`ngrams` size is hardcoded to `n=3`** — the URL (`main/urls.py:28`) doesn't accept an `n` parameter, so changing n requires a code change (the view function itself does default to `n=3` and would accept it if the URL were extended).
- **`ALLOWED_HOSTS = []`** is hardcoded in `config/settings.py:33`, not env-driven — will reject all requests as soon as `DEBUG=False` in any real deployment; must be fixed before going to production.
- **File upload validation**: `DocumentForm.clean()` requires either `content` text or a `file` (else `ValidationError`); `upload_document` view decodes uploaded files as UTF-8 and surfaces a form error on `UnicodeDecodeError` rather than crashing — reasonably defensive.
- **No automated tests anywhere** in the repo (`main/tests.py` is an empty stub) — all behavior above is unverified except by manual testing; changes should be manually smoke-tested against the corpus report pages.
- **No `STATIC_ROOT`** — fine today (no local static files, Bootstrap is CDN CSS-only, no JS bundle loaded either — some Bootstrap JS components like dropdowns/modals won't function if ever added to a template without also adding the JS bundle), but blocks `collectstatic`/production static serving the moment any local static asset is introduced.

---

## 6. Migration / Handover Guide

### Must be configured manually
1. **Python + venv** — project has been run under Python 3.14.x (per `info/new_device.md`); no `.python-version` pin exists in-repo.
2. **`.env` file** — copy `.env.example` → `.env` at repo root, fill in `SECRET_KEY` (generate a fresh one, don't reuse) and the `DB_*` vars (see table in §4).
3. **PostgreSQL** — create a database + role matching your `.env` (`DB_NAME`/`DB_USER`/`DB_PASSWORD`); no old data needs migrating, the app has no seed/fixture data — Django migrations recreate schema only.
4. **Superuser** — `python manage.py createsuperuser`, needed for `/admin/` and to exercise any `@login_required` corpus feature.
5. **Deployment infrastructure** — does not exist yet (no Dockerfile/Procfile/CI). Must be authored from scratch for any hosted environment; also revisit `ALLOWED_HOSTS`, `EMAIL_BACKEND`, and `STATIC_ROOT`/`MEDIA_ROOT` (all currently dev-only defaults) before deploying with `DEBUG=False`.

### Handled automatically
- Schema creation via `python manage.py migrate` (4 migration files, standard schema-only, no custom data migrations).
- `Token` indexing on document creation (post_save signal) — but **not** on later content edits (see §5 — rerun `retokenize` manually when needed).
- Dependency installation via `pip install -r requirements.txt` (flat file, pinned versions, no lockfile tooling).

### Steps (condensed)
```bash
git clone <repo> && cd KUIS
python -m venv venv && source venv/bin/activate      # Windows: venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env                                  # then fill in SECRET_KEY + DB_*
# provision a Postgres role + database matching .env
python manage.py migrate
python manage.py createsuperuser                       # optional but recommended
python manage.py runserver
```

---

## 7. Developer Quick Start

1. `git clone <repo> && cd KUIS`
2. `python -m venv venv && source venv/bin/activate`
3. `pip install -r requirements.txt`
4. `cp .env.example .env` — set `SECRET_KEY` and the `DB_*` vars to point at a local Postgres database you've created (see §6.3)
5. `python manage.py migrate`
6. `python manage.py runserver` → open `http://127.0.0.1:8000/`
7. Register a user (or `createsuperuser` + log in), go to **Corpus** → **Upload New Document**, paste/upload one of the sample files in `info/*.xml`, then explore Word Frequency / Collocations / N-grams / KWIC from the corpus dashboard.

---

## Flagged uncertainties (do not treat as fact without verifying)

- Whether `DB_ENGINE` can simply be swapped to `django.db.backends.sqlite3` for a zero-install local dev DB — plausible given `django-environ` config, but **not tested anywhere in the repo**; `psycopg2-binary` is a hard requirement in `requirements.txt` regardless.
- The full taxonomy of `features` error-type codes (e.g. `ktinf`) is **not documented anywhere in code** — only inferable from the two sample XML files in `info/`. A dedicated error-types tree document is expected separately.
- Whether uploaded `.txt` files are expected to always follow the corpus XML schema, or plain prose is also a legitimate input — the code supports both (via the flat-tokenize fallback) but this isn't stated as an explicit product decision anywhere.
