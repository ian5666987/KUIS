# Author (person-level) metadata + author filter facets

**Status: ✅ Shipped (backend).** Migration `0015`; `metadata/authors/*.csv` tracked. **Not yet applied or loaded on `corpus_db`** — same state as `0014`, see **Loading** below. KUIS-FE controls for the two new facets are not built yet; the Django surface renders them.

Companion to `docs/metadata-catalogue-plan.md`, which covers the *file*-level catalogue and the filter mechanism this feature extends. Matching rules: `docs/metadata-matching.md`.

## What this is

The catalogue answers *about the file* — university, year, grade, topic. It cannot answer *about the writer*. The client wanted corpus queries filterable by learner traits: mother tongue, gender, age, grade, duration of learning.

Three new CSVs supply that: `metadata/authors/{KUIS,OU,TUFS}.csv`, 20 columns, **one row per person per survey occasion** — a different grain from the catalogue's one-row-per-file. 439 rows describing 235 people.

Two of those traits ship as filter facets — **gender** and **mother tongue**. Grade already shipped with the catalogue at 100% coverage. The rest are imported and stored but deliberately not filterable; see **Why only two facets**.

## Shape of the source data

439 survey rows, 235 people, 3 universities. Profiled against the catalogue's 1,557 entries:

| University | Catalogue files | Linked to an author |
|---|---|---|
| TUFS | 527 | 527 (100%) |
| KUIS | 143 | 142 (99%) — `WAMO` has no survey row |
| OU | 66 | 66 (100%) |
| APU | 121 | 0 |
| CHU | 205 | 0 |
| SFC | 322 | 0 |
| SOP | 175 | 0 |
| **Total** | **1,557** | **733 (47%)** |

So **~53% of the corpus has no author metadata at all** — four of the seven universities were never surveyed. Those files are reachable only via each author facet's **"(no value)"** option. 110 of the 235 surveyed people have no catalogue file at all: survey dates run to 2026-01 while the catalogue covers 2023–2024.

Facet counts, weighted by file:

| Gender | files | | Mother tongue | files |
|---|---|---|---|---|
| Female | 446 (28.6%) | | Japanese | 712 (45.7%) |
| Male | 268 (17.2%) | | Malay | 21 (1.3%) |
| (no value) | 843 (54.1%) | | Tagalog | 8 (0.5%) |
| | | | (no value) | 837 (53.8%) |

`Arabic` and `Korean` are genuinely recorded tongues whose speakers have no catalogued essays, so `facet_values()`'s existing "only offer values with a linked document" rule correctly hides them.

## Key decisions

**- The join key is `(university, name_code)` — never `name_code` alone.** This is the correctness case the whole design turns on. `name_code` is **not unique across universities**: seven codes appear under two universities in the catalogue (`RAKA` → OU+CHU, `NAMI` → KUIS+APU, `BAYU` → SFC+APU, `WAIM` → APU+CHU, `SAHI` → KUIS+SOP, `AYU` → KUIS+APU, `RYON` → TUFS+CHU), and five of those have survey data under exactly one side. A `name_code`-only match would silently give **8 real files another person's demographics**. Enforced by `UniqueConstraint(university, name_code)` on `Author` and asserted directly in `main/tests.py::LoadAuthorMetadataCommandTests::test_linking_requires_university_AND_name_code`.

**- The filename is *not* parsed, even though it encodes everything.** `KUIS2023ACHI101` does decompose as university + year + name code + grade + file number — verified against all 1,559 catalogue rows, where every filename matches `^[A-Z]+20\d\d[A-Za-z]+\d{3}[a-z]*$` and the embedded university/year/grade agrees with the CSV columns in **100% of rows, zero mismatches**. But the columns already carry that information, indexed and verified, so a parser would add a failure mode for no gain. This is also consistent with `docs/metadata-matching.md`'s standing policy that no filename heuristic exists anywhere in the match path.

**- `Osaka` maps to `OU`, and an unmapped university is an error.** The surveys spell the university out; the catalogue uses the filename prefix. `UNIVERSITY_ALIASES` reconciles them. An unrecognised value **skips the row and exits non-zero** rather than passing through: an author under an unknown university can never link to a file and would contribute nothing to any facet — a failure with no symptom.

**- Two tables for two grains: `Author` holds only what is invariant, `AuthorSurvey` holds every occasion.** A person is surveyed up to six times. Putting a per-occasion value on `Author` would mean choosing one arbitrarily; putting an invariant on `AuthorSurvey` would mean six copies to keep in step. So `Author` carries gender and the mother-tongue set, and all 20 source columns live on `AuthorSurvey`, one row per occasion.

**- Mother tongue is a child table, not a column.** A person can have more than one (`TUFS/NEGSEI` has Japanese and Malay), so `AuthorMotherTongue` holds one row per tongue and "mother tongue is Japanese or Malay" is a membership test. Cells are split on `[、・,，/&;]` after NFKC normalisation — the source really does use the ideographic comma, the katakana middle dot and the ASCII comma, sometimes in one file.

**- The multi-valued facet is answered with `EXISTS`, not a join.** A join through the child table yields two rows for a two-tongue writer, and `main/views.py::_analysis_context` sums `token_count` over that same queryset — `.distinct()` dedupes rows, not an aggregate over them, so that writer's tokens would be counted twice. `EXISTS` asks the membership question without touching cardinality. `NOT EXISTS` is then the complete "(no value)" term on its own: it is true for a document with no catalogue row, one with no author, and an author with no tongue rows alike — the same three-in-one equivalence the column facets get from `IS NULL`. There was precedent for a facet outside the column map: `name_code` is already special-cased as a prefix `ILIKE`.

**- Semantics are unchanged: OR within a facet, AND across facets.** Including the multi-valued one. `(speaks Japanese OR Malay OR has no recorded tongue) AND (gender is Female) AND (grade is 2)`.

**- Conflicts resolve to NULL for single-valued traits and to the UNION for mother tongue.** There is no non-arbitrary winner between `Female` and `Male`, and a filter quietly believing the wrong one is worse than it admitting it doesn't know — so gender goes NULL and `Author.has_conflict` is set. Mother tongue takes the union instead, because union **invents nothing**: every tongue in it was asserted by some survey of that person. Resolving `{Japanese, Malay}` vs `{Malay}` to NULL would have cost the Malay facet all 21 of its files over one inconsistent row.

**- Every normalised value is stored as a raw/parsed pair.** `duration_learning_raw` + `duration_learning_months`, `survey_date_raw` + `survey_date`, and so on. The client corrects the CSVs at source, so the database has to stay reconstructible from what the CSV actually said — including where parsing failed.

**- A partial parse is treated as a failure, not a number.** `2、3 year(s)` means "two or three years". Matching only the `3 year(s)` and returning 36 months would be a guess presented as data, so anything left over after the recognised tokens is inspected: leftover containing a digit → `NULL` plus an error; leftover with no digit (in practice always `約`, "approximately") → the number is kept and the dropped hedge is reported.

**- Durations normalise to months as a `Decimal`.** year=12, month=1, week=1/4.345, day=1/30.44 — mean month lengths rather than 4 and 30, so "1 week" and "0.25 month" don't disagree by two days. Compound forms (`1 year(s)3 month(s)`) sum, so they need no second pattern. 436 of 439 rows parse.

**- `metadata/authors/` is a separate directory, and that is load-bearing.** `load_metadata_catalogue` globs `metadata/*.csv` **non-recursively** and hard-fails any CSV without a `File name` column. While these three files sat in `metadata/`, a bare `python manage.py load_metadata_catalogue` — the exact command in `info/quickstart.md` step 7 — aborted before importing anything, because `KUIS.csv` sorts first. Guarded now by `main/tests.py::test_only_catalogue_schema_csvs_live_directly_in_metadata`, which asserts over the real directory.

**- The validation report is the deliverable, and the command exits non-zero on an error.** The client owns the CSVs and corrects them at source, so an inconsistency that is never named never gets fixed. `--dry-run` reports without writing. The non-zero exit is so CI and deploy scripts notice bad source data rather than marching past it.

## Why only two facets

A person is surveyed twice per academic year (around May and January). Any value that changes between those two surveys **cannot be attached to a file**, because a filename records only year and grade — never which survey. Measured on the real data:

| Column | Invariant per person? | Stable within one academic year? | Ships as a facet |
|---|---|---|---|
| Gender | yes (1 conflict) | yes | **yes** |
| Mother tongue | yes (1 set disagreement) | yes | **yes**, multi-valued |
| Grade | n/a — on the catalogue row, 100% coverage | — | already shipped |
| Age | no | **no** — 87 of 290 person-year buckets hold two ages | no |
| Duration of learning | no | **no** — e.g. `KUIS/ACHI` grade 1 reads "1 month" (May 2023) and "9 months" (Jan 2024) | no |
| Difficulty self-rating | no | **no** — 64 of 291 buckets conflict | no |
| Proficiency test level | — | — | no — 91% blank (26 linked files) |
| Place / duration of stay | — | — | no — free text, 36 variants, 3 separators |
| Major | — | — | no — **not comparable across universities** |

Age carries exactly the same ambiguity as duration — the birthday falls between the two surveys — so it is deferred alongside it rather than shipped on the assumption that it is stable.

`Major` is worth its own note: KUIS and OU record a **language** (`Indonesian`), TUFS records a **faculty** (`School of International and Area Studies, Oceania`). Filtering on it would behave differently per university. It needs a controlled vocabulary from the client before it can be a facet.

All of these are **imported and stored** on `AuthorSurvey`. Promoting any of them later needs only a rule from the client for which survey occasion applies to a file, plus a facet registration — not a re-import.

## Inconsistent source data — for the data owner

Everything below is a **source-data issue to correct in the CSVs**, not a code bug. Reproduce the list any time with:

```bash
python manage.py load_author_metadata --dry-run
```

### Errors — a value could not be used

| File | Person | Issue | Effect |
|---|---|---|---|
| `KUIS.csv` | `KUIS/KONA` | Gender recorded `Female` in the 2023-05-16 survey and `Male` in 2024-01-10 | `gender` is NULL; this person's files answer "(no value)" on the gender facet |

### Warnings — imported, with the unusable part left NULL

| File | Person | Issue | Effect |
|---|---|---|---|
| `TUFS.csv` | `TUFS/NEGSEI` | Mother tongues `Japanese、Malay` in one survey, `Malay` in the other | imported as the union `{Japanese, Malay}`. **This person is the only Malay speaker with essays in the corpus, so the Malay facet's 21 files depend on this row** — worth confirming which is right |
| `TUFS.csv` | `TUFS/CHID` | `Duration of learning other FL (1)` = `2、3 year(s)` — "two or three years" | months NULL; needs a single value |
| `TUFS.csv` | `TUFS/HOMTAK` | `Duration of learning other FL (1)` = `2カ月` (Japanese notation) | months NULL; re-write as `2 month(s)` |
| `TUFS.csv` | `TUFS/ZOAR` | `Duration of learning other FL (1)` = `８ヵ月` (Japanese notation) | months NULL; re-write as `8 month(s)` |
| `TUFS.csv` | `TUFS/SUGCHIN` | `Duration of learning Indonesian` = `約2 year(s)` ("about") | read as 24 months, hedge dropped |
| `TUFS.csv` | `TUFS/KIRK` | `Duration of learning other FL (1)` = `約10 year(s)` | read as 120 months, hedge dropped |
| `OU.csv` | `OU/MADA`, `OU/OUJI` | `Date` = `11/27` with no year | `survey_date` and `academic_year` NULL — the occasion can't be placed in an academic year |

### Blanks (counts, not individually listed)

11 rows have no date, 13 no age, 11 no gender, 10 no mother tongue, 9 no grade, 12 no difficulty rating. Blank is a legitimate "not recorded" and imports cleanly; the counts are here so the owner can judge whether any are accidental.

### Coverage gaps

- **`KUIS/WAMO`** has catalogue files but no survey row — the only such case among the three surveyed universities.
- **APU (58 name codes), CHU (85), SFC (119), SOP (41)** have no survey data at all. This is what makes 53% of the corpus "(no value)" on every author facet. If survey CSVs for these exist, dropping them into `metadata/authors/` and re-running the command is the whole integration.
- **110 surveyed people have no catalogue file.** Expected — survey dates run to 2026-01 while the catalogue covers 2023–2024. They import as authors with no linked documents and never widen a result.

### Not an error, but worth the owner's confirmation

- **19 files whose filename-encoded grade disagrees with the survey's grade** for the same person and year — e.g. `TUFS2023UEMIS203.txt` is grade 2 by filename and column, while that person's 2023-05-11 survey says grade 3. Nothing depends on this today (grade comes from the catalogue, not the survey), and it would matter only if a per-survey facet like age or duration were promoted later.
- **`Major` vocabulary differs by university** — see **Why only two facets**.

## Loading

Order matters; the author import links against catalogue rows:

```bash
python manage.py migrate
python manage.py load_error_taxonomy        # MUST precede any upload
python manage.py load_metadata_catalogue
python manage.py load_author_metadata --dry-run   # read the report first
python manage.py load_author_metadata
```

Either import order converges — `load_author_metadata --relink` re-resolves links for rows that arrived later, mirroring `load_metadata_catalogue --relink`. Both commands are idempotent and upsert rather than replace, because `Author` rows carry resolved document links a delete-and-recreate would discard. Mother tongues are the exception: they are **replaced** per author on each import, so a tongue the client removes at source actually disappears.

Expected on the current data: **235 authors, 439 surveys, 226 mother-tongue rows, 733 linked files**, 1 error, 8 warnings, exit code 1.

`--dry-run` writes nothing but still derives and reports, which is where the error list above comes from.

> **`corpus_db` has had neither `0014` nor `0015` applied, and neither catalogue nor author data has been loaded there.** `docs/new-architecture.md` requires explicit confirmation before writing to that database. Treat production rollout as its own confirmed step.

## Where the code lives

| Concern | File |
|---|---|
| Models | `main/models.py` — `Author`, `AuthorMotherTongue`, `AuthorSurvey`, `DocumentMetadata.author` |
| Migration | `main/migrations/0015_author_metadata.py` |
| Parsing, normalisation, linking | `main/author_metadata.py` |
| Command | `main/management/commands/load_author_metadata.py` |
| Django filter | `main/metadata_catalogue.py` — `FACET_FIELDS`, `FACET_ORM_PATHS`, `MULTI_VALUE_FACETS`, `_facet_q`, `_multi_value_facet_q`, `facet_values` |
| Dataplane filter | `dataplane/repositories/shared.py` — `_FACET_COLUMNS`, `_mother_tongue_condition`, `resolve_document_ids` |
| Filter/facet schemas | `dataplane/repositories/base.py`, `dataplane/schemas/metadata.py` |
| Facet values | `dataplane/repositories/postgres/metadata_repository.py` |
| Table mirrors | `dataplane/models/tables.py` (+ 3 registrations in `dataplane/tests/test_schema_drift.py`) |
| UI | `main/views.py` — `METADATA_FACET_LABELS`, `METADATA_FACET_HINTS`; `components/metadata_picker.html`, `components/context_bar.html` |
| Tests | `main/tests.py` — `LoadAuthorMetadataCommandTests`, `AuthorFacetFilterTests`; `dataplane/tests/test_metadata_router.py` |

Adding a third author facet is: one entry in `FACET_FIELDS` + `FACET_ORM_PATHS`, one in `_FACET_COLUMNS`, one field on `MetadataFilter`/`MetadataFilterParams`/`MetadataFacets`, one label, and one line in the repository's facet query. The filter *application* sites do not change.

## Explicitly deferred

- **Age, duration of learning, difficulty, proficiency, place/duration of stay, major as facets.** Stored, not filterable. Each needs a client decision — a survey-resolution rule for the first three, a controlled vocabulary for `Major`.
- **A `DocumentMetadata.author_survey` FK.** Would force exactly the resolution decision above. Nothing needs it yet.
- **Multi-valued `place_of_stay`.** Same child-table shape as mother tongue would work, but the vocabulary needs normalising first (36 variants, three separators, mixed granularity — `Jakarta` vs `Jakarta, Cilandak` vs `Java`).
- **A filename parser.** Verified to agree with the columns in 100% of rows, so it would only ever serve as a regression guard on future catalogue exports. Kept out of the match path.
- **An admin CSV upload endpoint.** `import_author_rows` is already split out from the command for it, same as `import_catalogue_rows`; an upload path still needs the `utf-8-sig`/CRLF handling `decode_uploaded_file` lacks.
- **Refactoring the filter core to a generic facet registry.** Five facets now enumerate themselves in roughly a dozen places across two surfaces. Worth doing — but not in the same change that added the first cross-table facet. Revisit when a sixth is requested.
- **KUIS-FE controls** for the two new facets. The API serves them (`/api/v1/metadata-facets` returns `genders` and `mother_tongues`); the Next.js picker doesn't render them yet.
