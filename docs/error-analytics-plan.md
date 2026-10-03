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

## Summary view: hierarchical breakdown

The Summary tab is an expandable tree over the *whole* taxonomy, not just the 4 top categories: expanding `gramatikal` shows its subcategories, expanding one of those shows its leaf codes, each row carrying its count, its share of all errors, and its share of its parent.

`GET /api/v1/error-summary` answers this in one scan of `DocumentErrorFreq` by adding `by_node` alongside the original `by_category` (kept as-is — it's the endpoint's published shape, and `by_node`'s top level carries the same numbers). Each entry is `{code, path, count, self_count}`, where **`count` is already subtree-inclusive**: the frontend never sums descendants to render a parent, it only divides a count by a total to get a percentage. Nodes with no errors in the selected corpora are absent from the response, and the frontend prunes those branches rather than rendering a wall of zeroes — the filter picker above the table is what exists to show what *could* be selected.

The roll-up to ancestors happens in `_error_query.py::compute_error_summary` in Python, not in a recursive CTE, because `path` already spells out every ancestor's code (path segments ARE codes, and codes are unique taxonomy-wide) — so walking upward needs no second query and no self-join, and the grouped row set is bounded by the taxonomy's ~90 nodes rather than by the annotation count.

`self_count` exists because `DocumentErrorFreq` rows point at whichever node an annotation's `features` resolved to, which **can be a non-leaf** (`features="eror;leksikal"` is real data — see `dataplane/tests/conftest.py`'s `CORPUS_XML`). When a category has errors of its own, its children genuinely don't sum to its total, so the table says so inline (`(N not sub-classified)`) instead of letting the arithmetic look broken. Note this is also the one place the summary and Error Frequency/EPIC disagree by design: `resolve_leaf_node_ids` filters `is_leaf = TRUE`, so a non-leaf-resolved annotation is counted by the summary but never matched by the two search features.

## Verification

139/139 Django tests, 115/115 dataplane tests (summary roll-up, self-vs-subtree counts, root exclusion, by_node/by_category agreement, and the empty-document-set case are covered in `test_error_analytics_repository.py::TestSummary` and `test_error_frequency_router.py`).

Checked against real `corpus_db` data through the running data plane: 32 errors over 19 nodes, with every category's children summing exactly to its own total (`ejaan` 18 = 10+4+3+1; `gramatikal` 11 = 3+3+3+2) and the top level summing to `total`. The frontend table was server-rendered against that live response in both collapsed and fully-expanded states — correct nesting at all three depths, biggest-first ordering within each level, both percentage columns, and `lainnya` (zero errors) pruned.

End-to-end checked against real `corpus_db` data: category cascade, cross-branch union, and whole-tree-root queries all returned the expected counts. `npm run build`/`npm run lint` clean; the route resolves correctly on the dev server.

**Not verified**: actual browser click-through (no browser tool available in either session) — the indeterminate-checkbox visual state, and clicking the summary tree's disclosure toggles, are unconfirmed (the summary tree's expanded output was verified by server-rendering it, not by clicking). A project-wide `retokenize` hasn't been run for real (dry-run showed nothing in the current `corpus_db` needs it).
