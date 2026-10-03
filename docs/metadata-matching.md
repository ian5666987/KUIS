# Matching documents to their metadata catalogue entry

**Status: ✅ Shipped.** Migration `0014` is applied in test environments only — not yet on `corpus_db`. Feature context: `docs/metadata-catalogue-plan.md`.

## What this is

The catalogue (`metadata/*.csv`) describes files by name — `TUFS2023KOMSHI314.txt`. Uploaded corpus files are `.xml`, sometimes with a suffix the catalogue has never heard of: the real sample on disk is `KUIS2023FUA201-Eror.xml` against a catalogue row for `KUIS2023FUA201.txt`. This is the rule that decides whether a given `Document` *is* a given catalogue row.

Nothing here is best-effort guessing. A document either matches exactly one entry or matches none, and matching none is a fully supported state — the document stays usable and simply reads as having no metadata.

## The rule

A document's **match key** is, in order of preference:

1. the basename of its own `<header><textfile>` — `KUIS/KUIS2023FUA201.txt` → `KUIS2023FUA201.txt`;
2. failing that, `Document.title`.

Either way the key is then normalized: take the basename, strip **exactly one** extension, lowercase, trim. `KUIS/A2023B.txt`, `A2023B.XML` and `  a2023b.txt  ` all land on `a2023b`. A catalogue row's `match_key` is the same function applied to its `File name`, stored at import so the join is a plain indexed equality.

## Key decisions

- **The header wins over the filename.** It travels inside the document, so it survives renaming, and it is the only thing that reconciles the two naming schemes actually in use — `-Eror.xml` on disk vs `.txt` in the catalogue. Matching on it means **no suffix-stripping heuristic exists anywhere**, which matters because a heuristic would silently mis-link files whose names genuinely differ. `main/corpus_parsing.py` had never read `<header>`; `extract_textfile_name` is the first thing that does, and it deliberately does *not* gate on `looks_like_structured_xml`, whose `<body>` requirement is a tokenization concern, not a header one.
- **Exactly one extension is stripped, and nothing else is normalized.** That single rule is what makes `.txt` (catalogue) and `.xml` (upload) meet. `A2023B-Eror.xml` normalizes to `a2023b-eror`, *not* `a2023b` — by design; the header is what resolves that case, not the normalizer.
- **The title fallback is weaker than it looks.** `Document.title` is the uploaded filename only on the four corpus-upload paths. On `main/views.py::upload_document` and `main/api_documents.py::DocumentUploadView` it is free text the user typed. A document created through those paths matches only by luck, or by its header.
- **Resolution runs in both directions, because neither side can be assumed to exist first.** `main/document_ingest.py::get_or_create_document` links a newly created document against the already-loaded catalogue (synchronously — a document must be filterable the moment it exists, same reasoning as `content_hash`); `load_metadata_catalogue` links each imported row against already-uploaded documents. `--relink` re-runs only the second direction.
- **A claimed entry is never reassigned.** `DocumentMetadata.document` is a `OneToOneField`, so moving it would orphan the document holding it. Two documents legitimately mapping to one entry is a data question for a human, not something to settle by last-write-wins.
- **Only a NULL link is ever filled in.** Neither direction overwrites an existing link, so re-importing is safe and idempotent.
- **A dedup hit does not re-link.** Content-hash dedup (`docs/content-hash-dedup.md`) returns the *existing* document, which was linked when it was first created. This has a real consequence: if two catalogue files hold byte-identical content, only one `Document` exists, only one entry can claim it, and the second stays unlinked — reported by the import as awaiting upload.
- **Unparseable content is never an upload failure.** Bad XML, plain text, an empty `<textfile>` — all fall through to the title, and a missed match is reported, never raised.

## Where it lives

`main/metadata_catalogue.py` — `extract_textfile_name`, `normalize_match_key`, `match_key_for_document`, `link_catalogue_entry` (document → catalogue) and `relink_entries` (catalogue → document). A deliberately neutral module, same reasoning as `main/document_ingest.py`: the upload sites, the management command and `main/views.py`'s filter all import from it, and none should be reaching into another's internals.

Called from `main/document_ingest.py::get_or_create_document` (on create only) and `main/management/commands/load_metadata_catalogue.py`. Covered by `main/tests.py::MetadataMatchingTests`, including against the real `info/KUIS2023FUA201-Eror.xml`.

## Explicitly deferred

- **Fuzzy or near-miss matching.** Exact match on the normalized key only. An unmatched file is reported, not guessed at.
- **Matching on `Document.content_hash`** rather than a filename, which would survive renames entirely — the catalogue records no hash, so there is nothing to match against.
- **Reconciling byte-identical files under two catalogue names** (above) — the import reports the orphan; resolving it is manual.
- **Re-linking on title edit.** Nothing re-runs matching when a document is renamed; `--relink` is the manual path.
