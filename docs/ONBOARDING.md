# KUIS — Developer Onboarding Document

> Technical onboarding reference for the existing Django application, based on a full review of the codebase as of this writing. Reflects the codebase as-is — no redesign or new architecture proposed.

---

## 1. Project Overview

**KUIS** is a Django 6.0.4 web app for analyzing an Indonesian-language learner corpus. Admins upload XML files (plain text plus `<segment>` error annotations with corrections) and group them into named **corpora**; registered users then run corpus-linguistics reports across any multi-select of those corpora: word frequency, collocations, n-grams, and KWIC (keyword-in-context) search — each with an "original vs. corrected" text toggle.

- **Architecture**: classic server-rendered Django monolith. No REST API, no JS framework/SPA — Bootstrap 5.3 (CSS only, via CDN) + Django template forms with `onchange="this.form.submit()"` auto-submit patterns for filters.
- **Django structure**: one project package `config/` (settings/urls/wsgi/asgi) + one app `main/` (all models, views, forms, admin, templates, the XML parser, migrations, one management command). `info/` at repo root is **not code** — it holds a proposal PDF, two sample corpus XML files, and informal notes (not a README).
- **Two URL areas**: `/corpus/…` for curation (which files form which corpus) and `/analysis/…` for the reports. Analysis is never per-file — it always runs over the corpora currently selected in the session.
- **Auth**: Django's built-in `auth.User` (no custom user model), with a three-tier access model driven purely by `is_staff` — no roles or permissions tables. See §5.
- **Database**: PostgreSQL via `psycopg2-binary`, configured entirely through env vars (`django-environ`).
- **No deployment tooling exists yet**: no Dockerfile/Procfile, no CI/CD.

---

## 2. Feature Inventory

| Feature | Access | Description | Key files |
|---|---|---|---|
| **Public pages** (Home/About/Contact) | anyone | Static pages; Contact sends mail via `ContactForm` | `main/views.py` (`home`, `about`, `contact`), `main/forms.py` `ContactForm`, `main/templates/main/{home,about,contact}.html` |
| **Registration** | anyone | Signup via `UserCreationForm` subclass + required email; auto-logs in, redirects to dashboard | `main/views.py` (`register`), `main/forms.py` `RegisterForm`, `main/templates/registration/register.html` |
| **Login / Logout** | anyone | Custom `LoginView` subclass (Bootstrap-styled); stock `LogoutView` (POST-only, via nav form) | `main/views.py` (`CustomLoginView`), `config/urls.py`, `main/templates/registration/login.html` |
| **Dashboard / Profile** | registered | Minimal authenticated landing pages | `main/views.py` (`dashboard`, `profile`), `main/templates/main/{dashboard,profile}.html` |
| **Corpus list / detail** | registered | Browse corpora, their files, token counts, and which corpora overlap. Management buttons render only for admins | `main/views.py` (`corpus_dashboard`, `corpus_detail`), `main/templates/main/corpus_{dashboard,detail}.html` |
| **Create / edit / delete corpus** | **admin** | Name + description, tick existing files, and/or upload new XML files inline. Deleting a corpus keeps its files | `main/views.py` (`corpus_create`, `corpus_edit`, `corpus_delete`), `main/forms.py` `CorpusForm`, `main/templates/main/corpus_{form,confirm_delete}.html` |
| **Document upload** | **admin** | Standalone single-file upload (paste text or a UTF-8 file). `Document.save()` triggers token indexing via signal | `main/views.py` (`upload_document`), `main/forms.py` `DocumentForm`, `main/templates/main/upload_document.html` |
| **Analysis hub** | registered | Corpus multi-select + links into every feature; the selection is remembered in the session | `main/views.py` (`analysis_home`), `main/templates/main/analysis_home.html` |
| **Word Frequency** | registered | Top-20 word counts across the selected corpora | `main/views.py` (`word_frequency`), `main/corpus_parsing.py` `extract_word_streams`, `main/templates/main/word_frequency.html` |
| **Collocations** | registered | Top-20 adjacent word-pair counts | `main/views.py` (`collocations`), `main/templates/main/collocations.html` |
| **N-grams** | registered | Top-20 n-length word tuples; `n` is a 2–5 dropdown (`?n=`) | `main/views.py` (`ngrams`, `NGRAM_SIZES`), `main/templates/main/ngrams.html` |
| **KWIC** | registered | Concordance search, sortable, paginated, live XML parse; each line attributed to its source document | `main/views.py` (`kwic`, `_kwic_results`), `main/templates/main/kwic.html` |
| **KWIC Export CSV** | registered | Same query as KWIC, downloads CSV with a Document column | `main/views.py` (`kwic_export_csv`) |
| **KWIC (Fast)** | registered | Token-table-backed search (DB query, not live parse), scoped to the selected corpora | `main/views.py` (`kwic_search`), `main/models.py` `Token`, `main/templates/main/kwic_search.html` |
| **Django Admin** | admin | `Document` and `Corpus` registered (`CorpusAdmin` uses `filter_horizontal` for the M2M) | `main/admin.py` |
| **`retokenize` command** | CLI | Rebuilds all `Token` rows from current `Document.content`. Run after changing tokenization logic or editing a document's content | `main/management/commands/retokenize.py` |

---

## 3. Codebase / Functionality Map

```
config/
  settings.py   — all Django config (see §4)
  urls.py       — root routing: admin/, main.urls, login/logout
  wsgi.py, asgi.py — standard, unmodified

main/
  models.py            — Document, Corpus, Token; build_tokens(); post_save signal
  views.py             — all view functions/classes (see §2)
  urls.py              — app routing, split into /corpus/ and /analysis/ groups
  forms.py             — RegisterForm, ContactForm, CorpusForm, DocumentForm
  admin.py             — registers Document + Corpus
  apps.py              — standard AppConfig, no hooks
  corpus_parsing.py    — XML tokenizer: extract_word_streams()
  tests.py             — empty stub, no tests exist anywhere in the repo
  management/commands/retokenize.py
  migrations/          — 0001-0006 (0006 is a data migration)
  templates/
    main/base.html               — layout, nav, messages block, auth conditionals
    main/_corpus_selector.html   — shared corpus multi-select bar (see §7)
    main/analysis_home.html      — analysis hub
    main/corpus_dashboard.html, corpus_detail.html
    main/corpus_form.html, corpus_confirm_delete.html
    main/upload_document.html
    main/word_frequency.html, collocations.html, ngrams.html
    main/kwic.html, kwic_search.html
    main/{home,about,contact,dashboard,profile}.html
    registration/{register,login}.html
```

**Representative flow — Word Frequency:**
`GET /analysis/frequency/` → `views.word_frequency` → `_require_selection` (bounces to the hub if nothing is selected) → `_get_selected_documents(request)` resolves session corpus ids to a **`.distinct()`** document queryset → `_get_word_lists()` parses each document's XML into one word list per document → `Counter` → `.most_common(20)` → renders `word_frequency.html`, which includes `_corpus_selector.html` at the top.

**Representative flow — Fast KWIC:**
`GET /analysis/kwic/search/?word=…` → `views.kwic_search` → queries the pre-built `Token` table filtered by `document__in=<selected documents>` rather than re-parsing XML. `Token` rows are populated at `Document` creation by the `post_save` signal `create_tokens` → `build_tokens()` → `extract_word_streams`.

---

## 4. Data & Architecture

### Models (`main/models.py`)
- **`Document`**: `title`, `content` (raw XML/text), `uploaded_at`, `created_at`, `user` (FK→`User`, nullable), `token_count`, `token_count_corrected`. Index on `(user, uploaded_at)`.
- **`Corpus`**: `name` (unique), `description`, `created_by` (FK→`User`, `SET_NULL`), `created_at`, and **`documents = ManyToManyField(Document, related_name='corpora')`**. The M2M is deliberate — a file may belong to several corpora, and the client has accepted that overlap.
- **`Token`**: `document` (FK, `related_name='tokens'`), `word`, `position`, `mode` (`original`|`corrected`). Indexes on `word` and `(document, mode, position)`. One row per token per mode per document — denormalized for fast KWIC. **Unaffected by corpora**: tokenization is per-document, so corpus changes never require retokenizing.

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
- The parser falls back to flat (non-XML-aware) tokenization if the content isn't parseable XML or has no `<body>` — silent degradation, no error shown to the user.
- Segments can **nest**; `_walk_body` (`main/corpus_parsing.py`) only walks direct children of `<body>` — see §5.

### Migrations
`0001`–`0004` build `Document`/`Token`. `0005_corpus` adds the `Corpus` model and its M2M table. **`0006_legacy_corpus` is a data migration** that sweeps every pre-existing document into a corpus named `"Ungrouped (legacy import)"`, because a document in no corpus is invisible to every analysis feature. It is reversible (the reverse deletes that corpus).

### External services / background jobs
- **None real.** `EMAIL_BACKEND` is the console backend — the Contact form "sends" mail only to the dev console. No Celery/RQ/cron, no external APIs, no caching layer.

### Configuration (`config/settings.py`, via `django-environ` reading `.env`)
| Env var | Feeds | Default |
|---|---|---|
| `SECRET_KEY` | Django `SECRET_KEY` | none (required) |
| `DEBUG` | `DEBUG` (bool) | `False` |
| `DB_ENGINE` | `DATABASES.default.ENGINE` | `django.db.backends.postgresql` |
| `DB_NAME` / `DB_USER` / `DB_PASSWORD` | database credentials | none (required) |
| `DB_HOST` | `DATABASES.default.HOST` | `localhost` |
| `DB_PORT` | `DATABASES.default.PORT` | `5432` |

`ALLOWED_HOSTS = []` is **hardcoded** (not env-driven) — see §5. No `STATIC_ROOT`/`MEDIA_ROOT` configured (no static files exist; Bootstrap is CDN-only).

### Dependencies (`requirements.txt`)
`Django==6.0.4`, `django-environ==0.11.2`, `psycopg2-binary==2.9.12`, plus transitive pins `asgiref==3.11.1`, `sqlparse==0.5.5`, `tzdata==2026.1`. No Pipfile/pyproject.toml.

---

## 5. Access Control (RBAC)

Three tiers, driven entirely by Django's built-in `User.is_staff` flag. **There is no role or permission management UI** — an admin is simply a user with `is_staff=True`, set via `/admin/` or `createsuperuser`.

| Tier | Who | Can do |
|---|---|---|
| **Anonymous** | not logged in | Nothing but Home, About, Contact, Register, Login. Every feature URL redirects to `/accounts/login/?next=…`. |
| **Registered** | any logged-in user | All analysis features, the analysis hub, corpus list/detail (read-only), dashboard, profile. |
| **Admin** | `is_staff=True` | Everything above **plus** XML upload and corpus create/edit/delete, plus Django `/admin/`. |

**How it's enforced** — `main/views.py` defines:

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

Admin-only views: `upload_document`, `corpus_create`, `corpus_edit`, `corpus_delete`. Everything else user-facing is plain `@login_required`.

Templates gate admin affordances with `{% if user.is_staff %}` (see `corpus_dashboard.html`), so non-admins never see the upload/create/edit/delete buttons — but the server-side decorators are the actual boundary, not the template checks.

---

## 6. Edge Cases & Technical Considerations

- **A document in no corpus is invisible to every analysis feature.** This is by design (analysis is corpus-scoped), which is exactly why migration `0006` exists. `corpus_dashboard` shows an "unassigned files" warning to admins so new orphans get noticed.
- **Overlap is deduped via `.distinct()`** in `_get_selected_documents`. Without it, the M2M join returns a document once per matching corpus and it would be counted multiple times. Verified: selecting A∪B where B⊂A yields byte-identical output to selecting A alone.
- **Never flatten word lists across documents.** `_get_word_lists` returns *one list per document* precisely so collocation/n-gram/KWIC windows don't straddle a document boundary and invent pairs that don't exist in any real text. Any new sequence-based feature must fold per-document the same way.
- **The corpus selector needs its hidden `corpus_selection=1` marker.** Unchecking every box submits no `corpus` params at all, which is otherwise indistinguishable from "form not submitted" — without the marker the selection could never be cleared.
- **The selector also re-emits the current feature's filters** (`corrected`, `n`, `q`, `w`, `sort`, `word`) as hidden inputs. Drop those and changing corpora silently resets the n-gram size or wipes the KWIC query.
- **Stale session ids** are pruned in `_resolve_selected_corpus_ids` for *both* submitted and remembered ids — a stale browser tab can post a corpus id that has since been deleted.
- **Deleting a corpus never deletes its documents** — only the corpus row and its M2M links.
- **`Token` rows go stale on content edits.** They're built by the `post_save` signal on `Document` **creation** only; editing a document's content afterward leaves `Token`/fast-KWIC data out of sync. Run `python manage.py retokenize`.
- **`Correction` vs `correction` attribute casing.** Sample files use both; `_walk_body` only reads capital-C `Correction`, so lowercase-attribute segments contribute nothing to the corrected word stream.
- **Nested `<segment>` elements are only partially walked** by `_walk_body` (direct children of `<body>` only). Any future error-analysis feature should traverse with `body.iter('segment')` instead.
- **Malformed XML degrades silently** to flat regex tokenization, with no user-facing warning.
- **Performance**: analysis re-parses XML for every selected document on every request, so cost scales with the number of selected files. Fine at current volumes; `Token` is already the indexed fast path if word frequency ever needs `.values('word').annotate(Count('id'))` instead.
- **`ALLOWED_HOSTS = []`** is hardcoded in `config/settings.py`, not env-driven — it rejects all requests once `DEBUG=False`, and it also blocks Django's test client (override it in-process when testing). Must be fixed before any real deployment.
- **No automated tests** (`main/tests.py` is an empty stub). The dedupe and cross-document-boundary behaviors above are silent-wrong-answer risks and are the first things worth a `TestCase`.

---

## 7. The Corpus Selector Pattern

`main/templates/main/_corpus_selector.html` is included at the top of every analysis template and is what makes the selection survive navigation:

- It is a `method="get"` form with **no `action`**, so it submits back to whatever feature page it's currently on.
- Checkboxes auto-submit via the codebase's `onchange="this.form.submit()"` idiom.
- `views._resolve_selected_corpus_ids` writes the result to `request.session['selected_corpus_ids']`, so every other feature picks it up with no URL params at all.
- Because ids also travel in the query string when submitted, feature URLs stay shareable.

To add a new analysis feature, follow the existing shape: `@login_required` → `_require_selection(request)` → `_get_selected_documents(request)` → `_get_word_lists(...)` → build a `Counter` → merge `_analysis_context(request, documents)` into the template context → include the selector partial in the template.

---

## 8. Migration / Handover Guide

### Must be configured manually
1. **Python + venv** — the project runs under Python 3.14.x (per `info/new_device.md`); no `.python-version` pin exists in-repo.
2. **`.env` file** — copy `.env.example` → `.env`, fill in `SECRET_KEY` (generate a fresh one) and the `DB_*` vars.
3. **PostgreSQL** — create a database + role matching your `.env`. No seed data exists; migrations recreate schema only.
4. **Admin user** — `python manage.py createsuperuser`. **Required**: without an `is_staff` user nobody can upload files or create corpora, which means no analysis is possible at all.
5. **Deployment infrastructure** — none exists (no Dockerfile/Procfile/CI). Also revisit `ALLOWED_HOSTS`, `EMAIL_BACKEND`, and `STATIC_ROOT`/`MEDIA_ROOT` before deploying with `DEBUG=False`.

### Handled automatically
- Schema + the legacy-corpus data migration via `python manage.py migrate`.
- `Token` indexing on document creation (post_save signal) — but **not** on later content edits.
- Dependency installation via `pip install -r requirements.txt`.

---

## 9. Developer Quick Start

1. `git clone <repo> && cd KUIS`
2. `python -m venv venv && source venv/bin/activate` (Windows: `venv\Scripts\Activate.ps1`)
3. `pip install -r requirements.txt`
4. `cp .env.example .env` — set `SECRET_KEY` and the `DB_*` vars against a local Postgres database you've created
5. `python manage.py migrate`
6. `python manage.py createsuperuser` — **not optional**, see §8.4
7. `python manage.py runserver` → open `http://127.0.0.1:8000/`
8. Log in as the superuser → **Corpora** → *Create New Corpus* → name it, tick existing files and/or upload XML from `info/*.xml` → **Analysis** → tick one or more corpora → run Word Frequency / Collocations / N-grams / KWIC.

---

## Flagged uncertainties (do not treat as fact without verifying)

- Whether `DB_ENGINE` can simply be swapped to `django.db.backends.sqlite3` for a zero-install local dev DB — plausible given the `django-environ` config, but **not tested**; `psycopg2-binary` is a hard requirement in `requirements.txt` regardless.
- The full taxonomy of `features` error-type codes (e.g. `ktinf`) is **not documented anywhere in code** — only inferable from the two sample XML files in `info/`. A dedicated error-types tree document is expected separately, after which an "error frequency" feature is planned.
- Whether uploaded files are expected to always follow the corpus XML schema, or plain prose is also legitimate — the code supports both (via the flat-tokenize fallback) but this isn't stated as an explicit product decision.
