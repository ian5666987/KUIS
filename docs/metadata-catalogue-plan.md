# Document metadata catalogue + secondary filters

**Status: ✅ Shipped.** Backend (Django + dataplane) and KUIS-FE. `metadata/*.csv` is now tracked; migration `0014` is not yet applied to `corpus_db` and the catalogue is not yet loaded there — see **Loading** below for the sequence. Matching rules: `docs/metadata-matching.md`.

## What this is

Every analysis feature scoped on one axis: which corpora are selected. There was no way to ask "frequency over grade-3 writing only" or "KWIC across TUFS documents" — the information existed, as two CSV catalogues totalling 1,559 rows (1,557 distinct filenames), but lived outside the database.

The catalogue is now a table, each row linked to its uploaded `Document`, and University / Year / Grade / name code are a **secondary filter** that composes with corpus selection on every analysis feature, on both surfaces. Documents the catalogue doesn't cover stay fully usable; they drop out when a filter is applied unless the user ticks that facet's **"(no value)"** option.

The source data's quirks drive several decisions below: CRLF endings with no trailing newline; `File name` always ends `.txt` while uploads are `.xml`; `File name` is **not** unique (`OU2023BETA202.txt` and `OU2023OUSA202.txt` each appear twice with conflicting data); `Topic` ↔ `Topic (English)` is not 1:1 (`tokoh` is glossed two ways, "dance" maps to two Indonesian topics). 7 universities, Year ∈ {2023, 2024}, Grade ∈ 1–4, ~106 topics, 422 name codes.

## Key decisions

**- Match on the document's own `<header><textfile>`, falling back to its title.** The catalogue says `KUIS2023FUA201.txt`; the file on disk is `KUIS2023FUA201-Eror.xml`; the header inside that file says `KUIS/KUIS2023FUA201.txt`. The header reconciles them and means no suffix heuristic exists anywhere. Resolution runs in both directions, since neither side can be assumed to exist first. **The rules, the edge cases and their reasoning are their own reference: `docs/metadata-matching.md`** — that file is the authority, not this one.

**- One catalogue table holding a nullable FK to Document, not a sidecar on Document.** The catalogue is the authority and must load independently of upload order — all 1,557 entries can exist before a single file is uploaded, and a file can be uploaded before its row exists. So resolution runs in both directions: `get_or_create_document` links a new upload against the loaded catalogue, and the import command links each row against already-uploaded documents. Putting the FK on the catalogue side also leaves `main_document` untouched (nothing to re-mirror in `dataplane/models/tables.py`'s `document`), and `OneToOneField` enforces at DB level that two entries can't claim one Document — which matters, because `uniq_document_content_hash` collapses byte-identical uploads into a single row several catalogue filenames could otherwise point at. An entry already claimed is never moved: reassigning would orphan the other document.

**- A LEFT OUTER JOIN makes the "(no value)" option one expression instead of two branches.** Joining the catalogue from the document side, `university IS NULL` is true in *both* required cases — an entry whose field is empty, and a document with no entry at all (the outer join supplied the NULLs). This is the single reason the feature needed no special-casing per surface, and no "documents without metadata" code path. Django's ORM reaches the same thing: `catalogue_entry__university__isnull=True` promotes the join to LEFT OUTER. Integer columns get no `= ''` test — comparing one to an empty string is a type error in Postgres.

**- The filter is applied at exactly one point per surface.** `dataplane/repositories/shared.py::resolve_document_ids` for the seven dataplane endpoints, `main/views.py::_get_selected_documents` for the Django pages. Both already resolved a corpus selection to documents, so **no repository, SQL builder, worker task or Django view changed** — and all five `*_export_csv` routes inherited the filter for free. The per-repository alternative would have touched 9 ABC signatures, 6 repositories and 6 query builders, including `kwic_repository._match_query` where the documented alias trap lives. `DocumentWordFreq`/`DocumentNgram`/`DocumentErrorFreq` are per-document grain, so a narrowed id list just sums over fewer rows; an empty list is already every query builder's "nothing to compute" guard, so a filter matching nothing needed no new short-circuit.

**- Last row wins on conflicting duplicates, and the import says so.** `update_or_create` keyed on the normalised match key. Row order in the file silently decides those two filenames' data, so the command names them and their line numbers in a `WARNING` block rather than letting it pass unnoticed. A byte-identical repeat is reported separately as harmless. Values are otherwise stored verbatim: normalising the inconsistent topic glosses would hide a data-quality problem that belongs upstream.

**- `__none__` as the sentinel, inside each facet's own repeated param.** `?university=TUFS&university=__none__` rather than a parallel `university_unset=true`: one param per facet, and it mirrors how the control behaves — just another checkbox in the list. Consequence accepted: `year` and `grade` are declared `list[str]` and coerced in the service layer, since the sentinel isn't an int. Junk on a numeric facet is dropped rather than 422-ing; the filter is a navigation control, so a stale URL should widen the result, never break the page.

**- Query string only on the Django surface, no session.** Corpus ids live in `SELECTED_CORPORA_SESSION_KEY` because repeating them in every URL would grow without bound. Three short facets won't, and keeping them in the URL makes a filtered analysis shareable. `templatetags/kuis.py`'s `hidden_params`/`qs` then carry them through pagination, sort links and export for free — but `context_bar.html`'s `hidden_params exclude` **must** name every facet, or the form submits each value twice (once hidden, once from the real checkbox). The results toolbar's own forms should carry them as hidden inputs; that's what stops a text filter from wiping the metadata filter.

**- Facet values come only from entries that have a linked document.** Offering "University: OU" when no OU file is uploaded hands the user a guaranteed-empty filter. Global rather than scoped to the current corpus selection, matching `/api/v1/taxonomy` — a scoped list would reshuffle the checkboxes every time a corpus is ticked.

**- `Number of words` is stored but not filterable.** It's a third, independent notion of length alongside `Document.token_count`/`token_count_corrected`, which come from `corpus_parsing.py`'s own tokenizer and legitimately disagree. A length filter arguably belongs on `token_count`, which is a separate question.

## Data model

```python
class DocumentMetadata(models.Model):
    source_filename = models.CharField(max_length=200)        # CSV "File name" verbatim
    match_key = models.CharField(max_length=200, unique=True)  # basename, one extension off, lowered
    document = models.OneToOneField(
        Document, on_delete=models.SET_NULL, null=True, blank=True, related_name='catalogue_entry',
    )
    university = models.CharField(max_length=50, null=True, blank=True)
    year = models.IntegerField(null=True, blank=True)
    grade = models.IntegerField(null=True, blank=True)
    topic = models.CharField(max_length=100, null=True, blank=True)
    topic_en = models.CharField(max_length=100, null=True, blank=True)
    word_count = models.IntegerField(null=True, blank=True)
    name_code = models.CharField(max_length=50, null=True, blank=True)
    source_file = models.CharField(max_length=100)
    imported_at = models.DateTimeField(auto_now=True)
```

Migration: `main/migrations/0014_documentmetadata.py` (`CreateModel` only), indexes on `university`, `(year, grade)` and `name_code`. `SET_NULL` not `CASCADE` — documents are never hard-deleted today (`docs/user-management-plan.md`), and the row is source data that should outlive one. `match_key` is stored rather than derived so the join is an indexed equality; `source_filename` is deliberately not unique, since `match_key` is the real constraint. `related_name='catalogue_entry'`, not `metadata`, which would read as a sibling of Django's `_meta` and SQLAlchemy's `metadata`. Every analysis field is nullable because the "(no value)" filter needs one representation of empty — the importer stores a blank cell as `None`, never `''`.

Mirrored as `document_metadata` in `dataplane/models/tables.py` and registered in all three places in `dataplane/tests/test_schema_drift.py` (import list, `SA_TABLES`, `parametrize`) — one without the others silently defeats the guard.

## Loading

```
python manage.py load_metadata_catalogue              # every metadata/*.csv
python manage.py load_metadata_catalogue --file X.csv
python manage.py load_metadata_catalogue --dry-run    # report only, write nothing
python manage.py load_metadata_catalogue --relink     # re-resolve FKs, read no CSV
```

Idempotent, like `load_error_taxonomy` and `seed.py`, and for the same reason: catalogue content gets corrected and extended independently of the schema. Unlike `load_error_taxonomy` it **upserts** rather than replacing the table, because these rows carry resolved `document` FKs a delete-and-recreate would throw away. Opened with `newline=''` and `utf-8-sig` — the first is what keeps the CRLF endings from leaving a stray `\r` on every `Name code`, the second tolerates the BOM an Excel re-export adds. Headers are matched case- and whitespace-insensitively. A non-numeric year/grade/word count drops that one value to `None` and reports it, rather than failing a row whose other fields are still worth having.

## API

| Method | Path | Permission |
|---|---|---|
| GET | `/api/v1/metadata-facets` | authenticated |
| GET | `/api/v1/{frequency,collocations,ngrams,kwic,error-frequency,epic,error-summary}` | authenticated |
| GET | `/api/documents/` | authenticated |

All seven analysis endpoints gained `university`, `year`, `grade` (repeated, OR within a facet, AND across facets) and `name_code` (prefix, case-insensitive, LIKE wildcards escaped). No staff gate on filtering — `docs/error-analytics-plan.md` set that precedent; the importer is admin-only by being a management command. `/api/documents/` now nests each document's entry read-only (writes go through the command; the CSV is the authority) and takes `?has_metadata=false` to find exactly the files the catalogue misses.

**Implementation.** `MetadataFilter`/`MetadataFacets`/`MetadataRepository` next to `ErrorFilter` in `dataplane/repositories/base.py`. The filter params are the one shared **request** schema in `dataplane/schemas/` — a `Depends()`-able `MetadataFilterParams`, since declaring them inline would mean 28 duplicated `Query()` declarations; the three response-only docstrings in `schemas/{ranked,kwic,error_analytics}.py` were amended to name that exception. The facets endpoint copies the taxonomy trio at each layer. Param names match the Django view's own GET params, same convention `routers/frequency.py` notes for `q`/`match`/`sort`/`dir`.

## Where it lives

**Backend** (`KUIS`): `main/metadata_catalogue.py` (new, neutral — the match-key formula, the importer, the filter vocabulary, following `main/document_ingest.py`'s own reasoning for existing); `main/{models,views,api_documents}.py`, `main/migrations/0014_documentmetadata.py`, `main/document_ingest.py` (links on create), `main/management/commands/load_metadata_catalogue.py`, `main/templates/main/components/{context_bar,metadata_picker}.html`, `main/static/main/kuis.css`; `dataplane/models/tables.py`, `dataplane/repositories/base.py`, `dataplane/repositories/shared.py`, `dataplane/repositories/postgres/metadata_repository.py`, `dataplane/{services,routers}/metadata.py`, `dataplane/schemas/metadata.py`, `dataplane/{dependencies,main}.py`, and the five services + seven routers that pass the filter through. The `UNSET`/`METADATA_UNSET` sentinel is duplicated across the Django/dataplane boundary rather than shared — the dataplane can't import Django code, and `api_corpus.py` sets the precedent for duplicating over reaching across surfaces.

**Frontend** (`KUIS-FE`): `lib/metadataFilter.ts` (the three encodings — Sets in state, comma-joined in the URL, repeated for the API), `components/features/analysis/{MetadataFilterProvider,MetadataFilterPicker}.tsx`, `hooks/fastapi/useMetadataFacets.ts`, `hooks/useMetadataFilterChange.ts`, plus `ContextBar.tsx` (new `showMetadataFilter` prop, following `showMode`'s precedent), `Providers.tsx`, `constants.ts`, `api/fastapi/types.ts`, and all **nine** query-string builders: the three hooks and the six separately hand-built URL-mirror/export strings in `RankedSearch.tsx`, `ErrorAnalytics.tsx`, `ngrams-search.tsx`, `kwic-search.tsx` and `kwic-fast-search.tsx` — a pre-existing duplication this feature has to keep in step with.

## Verification

184/184 Django tests (was 139), 146/146 dataplane tests (was 115). `makemigrations --check` clean. `npm run build` and `npm run lint` clean (the one remaining lint warning, `FileDropzone.tsx`'s `aria-invalid`, is pre-existing).

New coverage: `MetadataMatchingTests` (header beats title; the real `info/KUIS2023FUA201-Eror.xml` links via its header; unparseable content falls back; a dedup hit doesn't relink; a claimed entry isn't stolen), `LoadMetadataCatalogueCommandTests` (CRLF with no trailing newline; last-row-wins on the real conflicting filenames; idempotent rerun keeps resolved FKs; `--dry-run`; `--relink` both directions; BOM; the real `metadata/*.csv`), `MetadataFilterViewTests` and `DocumentMetadataApiTests` on the Django side; `test_metadata_filter.py` and `test_metadata_router.py` on the dataplane side, the latter parametrized over all seven endpoints for both the filter params and the `__none__` sentinel. The tri-state is asserted on both surfaces, including that `university=__none__` returns the empty-field document *and* the uncatalogued one, and that value-plus-unset partitions the corpus token total exactly.

Note the test commands both need `CELERY_TASK_ALWAYS_EAGER=1` (`.github/workflows/ci.yml` sets it; 16 Django tests and most dataplane tests fail without it), and the dataplane suite needs `DB_NAME` pointing at a database whose name contains "test" — its own fixture refuses otherwise.

Checked against a live `uvicorn` data plane on the isolated test database, not only through `TestClient`: all seven endpoints honour the filter, `/api/v1/metadata-facets` omits a value whose only entry has no linked document, AND-across-facets returns empty where it should, a junk numeric value widens instead of 422-ing, and a facet's value and its "(no value)" selection partition the corpus token total exactly (12 + 10 = 22). Against a scratch SQLite database the real catalogue imports as 1,557 entries from 1,559 rows with both conflicts named, and `info/KUIS2023FUA201-Eror.xml` links to `KUIS2023FUA201.txt` via its header.

**Not verified**: no browser click-through (no browser tool available this session) — the picker's own rendering inside `ContextBar` is unconfirmed visually, though the Django surface's equivalent markup is asserted in `MetadataFilterViewTests`. `load_metadata_catalogue` has **not** been run against `corpus_db`, and migration `0014` is **not** applied there — both still pending, since `docs/new-architecture.md` requires confirmation before writing to that database.

## Explicitly deferred

- **Topic / Topic (English) as filter facets.** Stored, imported and indexed, but ~106 and ~108 values need a searchable multi-select rather than a checkbox list. The facet mechanism is field-agnostic, so adding them is a name in `FACET_FIELDS`, one in `METADATA_FACETS`, and a UI control.
- **`Number of words` as a filter** — see the decision above.
- **Admin CSV upload endpoint.** Command only. `import_catalogue_rows` is already separated from the command for this, but an upload path needs the `utf-8-sig`/CRLF decode handling `decode_uploaded_file` doesn't do.
- **Facet counts in the picker.** Values only, no per-value document counts.
- **Reconciling `uniq_document_content_hash` orphans.** If two catalogue files hold byte-identical content, one Document exists and one entry can claim it; the second stays unlinked and the import reports it as awaiting upload.
- **Normalising the inconsistent topic glosses and capitalisation** (`Bali`, `Natal`, `pesona Indonesia`) — stored verbatim on purpose.
