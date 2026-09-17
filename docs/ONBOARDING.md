# KUIS — Developer Onboarding Document

> Technical onboarding reference for the existing Django application, based on a full review of the codebase as of this writing. Reflects the codebase as-is — no redesign or new architecture proposed.

---

## 1. Project Overview

**KUIS** is a Django 6.0.4 web app for analyzing an Indonesian-language learner corpus. Admins upload XML files (plain text plus `<segment>` error annotations with corrections) and group them into named **corpora**; registered users then run corpus-linguistics reports across any multi-select of those corpora: word frequency, collocations, n-grams, and KWIC (keyword-in-context) search — each with an "original vs. corrected" text toggle.

- **Architecture**: classic server-rendered Django monolith. No REST API, no JS framework/SPA. The interface is a self-contained stylesheet (`main/static/main/kuis.css`, design tokens + components, no framework and no CDN) plus one optional progressive-enhancement script (`main/static/main/kuis.js`): corpus search, bulk select, panel collapse, auto-submit and the busy indicator. Every page works with JavaScript disabled.
- **Django structure**: one project package `config/` (settings/urls/wsgi/asgi) + one app `main/` (all models, views, forms, admin, templates, template tags, static assets, the XML parser, migrations, one management command). `info/` at repo root is **not code** — it holds a proposal PDF, two sample corpus XML files, and informal notes (not a README).
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
| **Analysis hub** | registered | Corpus multi-select + scope statistics + links into every feature; the selection is remembered in the session | `main/views.py` (`analysis_home`), `main/templates/main/analysis_home.html` |
| **Word Frequency** | registered | Full ranked word counts with per-million normalisation; filter (contains/starts/ends/exact), sort by word or count, paginate | `main/views.py` (`word_frequency`), `main/corpus_parsing.py` `parse_document`, `main/templates/main/word_frequency.html` |
| **Collocations** | registered | Adjacent word-pair counts, same table controls | `main/views.py` (`collocations`), `main/templates/main/collocations.html` |
| **N-grams** | registered | n-length word tuples; `n` is a 2–5 segmented control (`?n=`), same table controls | `main/views.py` (`ngrams`, `NGRAM_SIZES`), `main/templates/main/ngrams.html` |
| **KWIC** | registered | Concordance search over a live XML parse; word or phrase, context window 1–15, sortable by left/right/file, paginated, each line attributed to its file | `main/views.py` (`kwic`, `_kwic_results`), `main/templates/main/kwic.html` |
| **Word index** (was "KWIC (Fast)") | registered | Same concordance view for a single word form, answered from the `Token` table instead of re-parsing; one query per document on the current page | `main/views.py` (`kwic_search`, `_word_index_matches`, `_hydrate_word_index`), `main/models.py` `Token`, `main/templates/main/kwic_search.html` |
| **CSV export** | registered | Every analysis has an `/export/` route that re-runs the same query with the same filter and sort, unpaginated | `main/views.py` (`*_export_csv`, `_csv_response`) |
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
  corpus_parsing.py    — XML tokenizer: parse_document() / extract_word_streams()
  tests.py             — interface smoke tests (rendering, selection, modes,
                         table controls, exports)
  templatetags/kuis.py — hidden_params / qs / sort_url / aria_sort tags,
                         per_million + bar_width filters (see §7)
  static/main/kuis.css — the whole interface: design tokens, then components
  static/main/kuis.js  — optional enhancements only; nothing depends on it
  management/commands/retokenize.py
  migrations/          — 0001-0006 (0006 is a data migration)
  templates/
    main/base.html               — layout, nav, messages block, auth conditionals
    main/components/             — the shared UI vocabulary (see §7)
      context_bar.html           — corpus selection + text mode, on every analysis page
      corpus_picker.html         — multi-select with search, bulk actions, metadata
      analysis_nav.html          — feature tabs
      frequency_results.html     — stats + toolbar + table + pagination for count analyses
      concordance_results.html   — shared KWIC table for both engines
      sort_header.html, pagination.html, state.html, mode_notice.html
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
`GET /analysis/frequency/` → `views.word_frequency` → `_require_selection` (bounces to the hub if nothing is selected) → `_get_selected_documents(request)` resolves session corpus ids to a **`.distinct()`** document queryset → `_count_words()` parses each document into one word list per document and folds them into a `Counter` → `_rank_counter()` applies the filter, the sort and the pagination shared by all three count analyses → renders `word_frequency.html`, which includes `components/context_bar.html` at the top and delegates the results to `components/frequency_results.html`. The matching `/export/` route runs the same pipeline without pagination.

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
- The parser falls back to flat (non-XML-aware) tokenization if the content isn't parseable XML or has no `<body>`. `parse_document()` returns an `is_structured` flag for exactly this case; the analysis views collect the affected titles and the UI names them in corrected mode, where the fallback would otherwise look like an identical result for no reason.
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
`Django==6.0.4`, `django-environ==0.11.2`, `psycopg2-binary==2.9.12`, plus transitive pins `asgiref==3.11.1`, `sqlparse==0.5.5`, `tzdata==2026.1`. No Pipfile/pyproject.toml. The interface adds no dependency: `django.contrib.humanize` (thousands separators) is the only new `INSTALLED_APPS` entry, and static files are served by `django.contrib.staticfiles` in development.

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
- **Malformed XML still degrades to flat regex tokenization**, but no longer silently: see `parse_document`'s `is_structured` flag and `components/mode_notice.html`.
- **Performance**: analysis re-parses XML for every selected document on every request, so cost scales with the number of selected files. Fine at current volumes; `Token` is already the indexed fast path if word frequency ever needs `.values('word').annotate(Count('id'))` instead.
- **`ALLOWED_HOSTS = []`** is hardcoded in `config/settings.py`, not env-driven — it rejects all requests once `DEBUG=False`, and it also blocks Django's test client (override it in-process when testing). Must be fixed before any real deployment.
- **Tests**: `main/tests.py` covers page rendering, selection persistence and clearing, overlap dedupe, the text-mode substitution, the table controls and every CSV export. Run them against SQLite without a Postgres instance: `DB_ENGINE=django.db.backends.sqlite3 DB_NAME=:memory: python manage.py test main`. Django adds `testserver` to the hardcoded empty `ALLOWED_HOSTS` automatically, so no override is needed.
- **Multi-line `{# … #}` template comments do not work.** Django's comment token is single-line only, so a multi-line `{# … #}` renders into the page as literal text. Use `{% comment %} … {% endcomment %}`.

---

## 7. The Context Bar Pattern

`main/templates/main/components/context_bar.html` is included at the top of every analysis template. It holds the two pieces of state that apply to every feature — **which corpora** and **original vs corrected** — and is what makes them survive navigation:

- It is a `method="get"` form with **no `action`**, so it submits back to whatever feature page it's currently on.
- It always carries the hidden `corpus_selection=1` marker. Unticking every box submits no `corpus` params at all, which is otherwise indistinguishable from "form not submitted"; without the marker a selection could never be cleared.
- Text mode is a pair of radios (`corrected=0|1`), not a checkbox: "original" is a real choice, not the absence of one. They auto-submit; corpus changes are applied with a button, because a reload per tick is slow and disorienting when selecting several.
- `views._resolve_selected_corpus_ids` writes the result to `request.session['selected_corpus_ids']`, so every other feature picks it up with no URL params at all. Ids also travel in the query string when submitted, so feature URLs stay shareable.

**Parameters travel by tag, not by hand.** `main/templatetags/kuis.py` is what keeps several forms on one page from discarding each other's state:

- `{% hidden_params exclude="q,match,page" %}` re-emits the whole current query string as hidden inputs minus the named keys. A form changes its own parameters and preserves everyone else's — including parameters added later, which is why no form has a hardcoded list of fields to carry.
- `{% qs page=3 %}` rewrites the current query string for a link (empty value drops the key). Pagination, sort headers, "clear filter" and every export link use it.
- `{% sort_url 'count' 'desc' %}` and `{% aria_sort 'count' %}` drive `components/sort_header.html`: clicking the active column flips direction, a new column starts at its natural direction and returns to page 1.

**To add a new analysis feature**, follow the existing shape: `@login_required` → `_require_selection(request)` → `_get_selected_documents(request)` → `_count_words(...)` (or your own fold) → `_rank_counter(request, counter, total)` for the table controls → merge `_analysis_context(request, documents, feature='…')` into the context → in the template, include `components/context_bar.html`, `components/analysis_nav.html` (add a tab), and `components/frequency_results.html`. Add the feature's `/export/` route with `_csv_response` and `_sorted_rows_for_export` so it exports like every other one.

**UI conventions worth keeping.** Numbers are right-aligned and tabular; sort direction and text mode are carried by a glyph and `aria-sort`/`aria-current`, never by colour alone; loading, empty and error are three distinct states (`components/state.html`, the `.notice` variants, and `body.is-loading`), because "still working" and "nothing found" must never look the same.

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
