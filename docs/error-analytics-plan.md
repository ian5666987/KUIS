# Error Analysis: Error Frequency + EPIC (Error Phrase In Context)

**Status: ✅ Shipped.** Two new analysis features, built on top of `new-architecture.md`'s previously-unbuilt Phase 7 design.

## What this is

`info/error.xml` defines a taxonomy of ~90 Indonesian-language error codes (root `eror` → 4 categories → optional subcategories → leaf codes, e.g. `eror;gramatikal;frasa-nomina;nomafx`). Real corpus documents already carry these as `<segment features="...">` annotations, which were previously never parsed or stored.

- **Error Frequency** — word-frequency's shape, but for erroneous *phrases*: `{phrase, frequency}`, filtered by error type.
- **EPIC (Error Phrase In Context)** — KWIC's shape, but centered on annotated errors instead of a searched phrase: left/keyword/right context plus the error code and correction text.

## Filter model

Both features share one filter: a flat, OR-combined list of taxonomy codes **at any depth** — a specific leaf (`ktinf`) or a whole category (`gramatikal`). In the UI this is a standard tri-state checkbox tree: checking a category cascades to every leaf underneath it (shown as an indeterminate dash when only some are selected), and checking several nodes from any branch unions their results.

This works with one mechanism, not two: a leaf's own `path` always ends with its own code as the last segment, so `concat(';', path, ';') LIKE '%;code;%'` against the tiny `ErrorTaxonomyNode` table matches *both* a direct leaf hit and every descendant of a checked category — no separate exact-match branch. Checking a whole category sends just that one code; the backend resolves the subtree in a single indexed query rather than the frontend enumerating every leaf (the frontend also collapses a fully-selected subtree back to its ancestor code before sending, in `lib/taxonomy.ts::compressSelection` — a pure size optimization, not a correctness requirement).

*(An earlier version of this design AND-combined a separate "category" axis for drill-down narrowing. Dropped once the actual tree-picker UI existed — AND-across-branches doesn't match how anyone expects a checkbox tree to behave.)*

## Data model

Three new, purely-additive Django models (`main/models.py`, migration `0012`): `ErrorTaxonomyNode` (the taxonomy, loaded from `info/error.xml` by `load_error_taxonomy`), `ErrorAnnotation` (one row per `<segment>`, extracted by `worker/tasks/error_annotations.py`), `DocumentErrorFreq` (precomputed per-category counts, used only by the summary view — Error Frequency itself queries `ErrorAnnotation` live, since phrase text isn't a bounded-enough dimension for an aggregate to help).

Getting real annotation data required fixing a bug in `main/corpus_parsing.py`: a `<segment>` nested inside another `<segment>` (how overlapping errors are represented) was silently dropped by the old tokenizer. Fixed as part of this work; `retokenize --dry-run` is available to preview the impact on existing documents before a real retokenize.

## Where it lives

**Backend** (`KUIS`): `main/{models,corpus_parsing,admin}.py`, `main/management/commands/{load_error_taxonomy,backfill_error_annotations,retokenize}.py`, `worker/tasks/error_annotations.py` (chained into `indexing.py`); `dataplane/repositories/base.py` (`ErrorFilter`, `ErrorAnalyticsRepository`, `TaxonomyRepository`), `dataplane/repositories/postgres/{_error_query,error_analytics_repository,taxonomy_repository}.py`, `dataplane/{services,routers,schemas}/{error_analytics,taxonomy}.py`. Endpoints: `GET /api/v1/error-frequency`, `/api/v1/epic`, `/api/v1/error-summary`, `/api/v1/taxonomy` (all `error_code=` repeated, OR-combined; no staff gate).

**Frontend** (`KUIS-FE`): `lib/taxonomy.ts` (tree-checkbox mechanics as pure functions), `components/features/analysis/{TaxonomyPicker,ErrorAnalytics,EpicTable,ErrorSummaryTable}.tsx`, `hooks/fastapi/{useTaxonomy,useErrorAnalytics}.ts`, wired into the analysis hub at `/analysis/error-analytics`.

## Verification

119/119 Django tests, 107/107 dataplane tests. End-to-end checked against real `corpus_db` data: category cascade, cross-branch union, and whole-tree-root queries all returned the expected counts. `npm run build`/`npm run lint` clean; the route resolves correctly on the dev server.

**Not verified**: actual browser click-through (no browser tool available this session) — the indeterminate-checkbox visual state in particular is unconfirmed. A project-wide `retokenize` hasn't been run for real (dry-run showed nothing in the current `corpus_db` needs it).
