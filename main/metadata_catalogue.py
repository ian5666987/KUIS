"""The document metadata catalogue: filename matching, CSV import, facets.

Feature context: docs/metadata-catalogue-plan.md. The matching rules this
module implements — the header-first key, what the normalizer does and
deliberately does not do, both resolution directions, and the edge cases —
are documented as their own reference in docs/metadata-matching.md, which is
the authority for them.

Deliberately a new/neutral module, for the same reason
main/document_ingest.py is one: the upload sites (main/views.py,
main/api_corpus.py, main/api_documents.py), the management command and
main/views.py's analysis filter all need parts of this, and none of them
should be reaching into another surface's internals to get it. Everything
here is importable by all of them.

Three things live here and nowhere else:

  * the match key formula (normalize_match_key / match_key_for_document) —
    one formula, so an upload and a catalogue import can never disagree
    about whether a given file "is" a given catalogue row;
  * the import itself (import_catalogue_rows), shared by the
    load_metadata_catalogue command and, later, an admin upload endpoint;
  * the filter vocabulary (FACET_FIELDS, UNSET) that the Django analysis
    views parse. The dataplane duplicates that vocabulary rather than
    importing it — it can't import Django code — see
    dataplane/schemas/metadata.py.
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import PurePosixPath, PurePath

from django.db import transaction
from django.db.models import Q

from .models import Document, DocumentMetadata

# The sentinel a filter uses to ask for "documents with no value for this
# facet" — which means BOTH a catalogue entry whose field is empty and a
# document with no catalogue entry at all. Chosen as a value inside the
# facet's own repeated param (?university=TUFS&university=__none__) rather
# than a parallel `university_unset=true` flag: one param per facet, and it
# mirrors how the control behaves (just another checkbox in the list).
# DUPLICATED in dataplane/schemas/metadata.py — keep the two in step.
UNSET = '__none__'

# The catalogue fields exposed as selectable filter facets. topic/topic_en
# are imported and stored but deliberately absent here: ~106 and ~108
# distinct values need a searchable multi-select, not a checkbox list (see
# the plan's "Explicitly deferred"). Adding one later is a line here plus a
# UI control — nothing else in the filter path is field-specific.
FACET_FIELDS = ('university', 'year', 'grade')

# Which of the above are integer columns: `= ''` is a valid "empty" test for
# a varchar and a type error for an integer in Postgres, so the two kinds of
# column need different unset predicates on both surfaces.
NUMERIC_FACETS = frozenset({'year', 'grade'})

# CSV header -> model field. The header is matched case-insensitively with
# whitespace collapsed (see normalize_header) so a re-export that changes
# capitalisation doesn't silently drop a column.
CSV_FIELD_MAP = {
    'file name': 'source_filename',
    'university': 'university',
    'year': 'year',
    'grade': 'grade',
    'topic': 'topic',
    'topic (translated into english)': 'topic_en',
    'number of words': 'word_count',
    'name code': 'name_code',
}

REQUIRED_CSV_HEADERS = ('file name',)

INTEGER_FIELDS = ('year', 'grade', 'word_count')


def normalize_header(name):
    return ' '.join((name or '').split()).strip().lower()


def extract_textfile_name(content):
    """The basename recorded in the document's own `<header><textfile>`, or
    None when there isn't one.

    This is the primary match key source, in preference to the uploaded
    filename, because it survives renaming and because it is the only thing
    that reconciles the two naming schemes actually in use: the catalogue
    says `KUIS2023FUA201.txt` while the file on disk is
    `KUIS2023FUA201-Eror.xml`, and the header inside that file says
    `KUIS/KUIS2023FUA201.txt`. Matching on the header means no `-Eror`-style
    suffix-stripping heuristics are needed anywhere.

    Deliberately does NOT gate on corpus_parsing.looks_like_structured_xml:
    that helper additionally requires a `<body>`, which is a tokenization
    concern. A header is readable whether or not the document has a body,
    and this function is only ever asked for the header.
    """
    if not content:
        return None

    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        # Plain text, or XML we can't parse — the caller falls back to the
        # uploaded filename. Never an upload failure: a missed match just
        # means the document reads as having no metadata.
        return None

    text = (root.findtext('header/textfile') or '').strip()
    if not text:
        return None

    # PurePosixPath, not PurePath: the recorded value uses forward slashes
    # ('KUIS/KUIS2023FUA201.txt') regardless of what platform wrote it, and
    # on Windows PurePath would not split it.
    return PurePosixPath(text).name


def normalize_match_key(name):
    """The one formula for the catalogue join key: basename, one extension
    stripped, lowercased.

    Strips exactly one extension so `.txt` (what the catalogue records) and
    `.xml` (what gets uploaded) land on the same key. No other
    normalization — in particular no suffix heuristics; extract_textfile_name
    already covers the one case that needs it, and guessing at suffixes would
    silently mis-link files whose names genuinely differ.
    """
    if not name:
        return ''

    # Accept either separator here: this runs on catalogue values (forward
    # slashes) and on uploaded filenames (whatever the browser sent).
    base = PurePath(str(name).strip().replace('\\', '/')).name
    stem = PurePosixPath(base).stem if '.' in base else base
    return stem.strip().lower()


def match_key_for_document(document):
    """The catalogue key for a Document: its `<header><textfile>` when it has
    one, else its title. See extract_textfile_name for why the header wins."""
    from_header = extract_textfile_name(document.content)
    return normalize_match_key(from_header if from_header else document.title)


def link_catalogue_entry(document):
    """Attaches `document` to its catalogue entry, if one is loaded. Returns
    the entry, or None when the catalogue has no row for it.

    Called synchronously at upload (main/document_ingest.py) rather than from
    the worker, so a freshly uploaded document is visible to metadata filters
    immediately — the same reasoning docs/content-hash-dedup.md gives for
    computing content_hash at upload time. It's one indexed lookup.

    An entry already claimed by a different document is left alone: `document`
    is a OneToOneField, so reassigning it would orphan the other document, and
    two documents legitimately mapping to one catalogue row (byte-identical
    content under two catalogue filenames) is a data question for a human, not
    something to resolve by last-write-wins.
    """
    key = match_key_for_document(document)
    if not key:
        return None

    entry = DocumentMetadata.objects.filter(match_key=key).first()
    if entry is None:
        return None

    if entry.document_id == document.id:
        return entry
    if entry.document_id is not None:
        return None

    entry.document = document
    entry.save(update_fields=['document'])
    return entry


@dataclass
class ImportReport:
    """What one import pass did. `conflicts` names the filenames that appeared
    more than once with differing data — last row wins (the plan's decision),
    so the caller has to be able to say which rows were silently overridden."""

    created: int = 0
    updated: int = 0
    rows_read: int = 0
    linked: int = 0
    unlinked: int = 0
    conflicts: dict = field(default_factory=dict)       # source_filename -> [line numbers]
    duplicates: dict = field(default_factory=dict)      # source_filename -> [line numbers]
    skipped: list = field(default_factory=list)         # (line number, reason)

    @property
    def total(self):
        return self.created + self.updated


def _coerce_row(raw, line_number, report):
    """One CSV row -> field dict, or None when it can't be used.

    Values are taken verbatim apart from whitespace stripping: the catalogue
    is the authority, and silently normalizing its inconsistencies ('tokoh'
    glossed two different ways, a handful of capitalised topics) would hide a
    data-quality problem that belongs upstream. An empty cell becomes None,
    not '' — so the "(no value)" filter has one representation to test for.
    """
    values = {}
    for header, value in raw.items():
        key = normalize_header(header)
        target = CSV_FIELD_MAP.get(key)
        if target is None:
            continue
        # .strip() also removes the trailing \r the catalogue's CRLF line
        # endings leave on the last column when a reader doesn't eat them.
        values[target] = (value or '').strip() or None

    if not values.get('source_filename'):
        report.skipped.append((line_number, 'no File name'))
        return None

    for name in INTEGER_FIELDS:
        if values.get(name) is None:
            continue
        try:
            values[name] = int(values[name])
        except ValueError:
            # A non-numeric year/grade/word count is dropped to None rather
            # than failing the row: the row's filename and text fields are
            # still worth having, and None is already a meaningful,
            # filterable state.
            report.skipped.append((line_number, f'non-numeric {name}: {values[name]!r}'))
            values[name] = None

    return values


def import_catalogue_rows(rows, source_file, dry_run=False):
    """Upserts catalogue rows and resolves each one's Document.

    `rows` is an iterable of dicts (csv.DictReader output). Upserts rather
    than replacing the table wholesale — unlike load_error_taxonomy, whose
    rows carry no foreign keys, these rows hold resolved `document` FKs that
    a delete-and-recreate would throw away.

    Resolution runs in BOTH directions across the feature: here, catalogue ->
    document for rows whose file is already uploaded; and in
    link_catalogue_entry, document -> catalogue for files uploaded later.
    Neither side can be assumed to exist first.
    """
    report = ImportReport()
    # match_key -> (field dict, line number). Building this first is what
    # implements last-row-wins while still being able to report which rows
    # were overridden; the catalogue has two filenames that appear twice with
    # genuinely different topic and word count.
    by_key = {}

    for line_number, raw in enumerate(rows, start=2):   # 1 is the header
        report.rows_read += 1
        values = _coerce_row(raw, line_number, report)
        if values is None:
            continue

        key = normalize_match_key(values['source_filename'])
        if not key:
            report.skipped.append((line_number, f'unusable File name: {values["source_filename"]!r}'))
            continue

        previous = by_key.get(key)
        if previous is not None:
            prev_values, prev_line = previous
            name = values['source_filename']
            report.duplicates.setdefault(name, [prev_line]).append(line_number)
            # Only a conflict when the rows actually disagree — a byte-identical
            # repeat is harmless and shouldn't be reported as data to fix.
            if prev_values != values:
                report.conflicts.setdefault(name, [prev_line]).append(line_number)

        by_key[key] = (values, line_number)

    if dry_run:
        existing_keys = set(
            DocumentMetadata.objects.filter(match_key__in=by_key.keys()).values_list('match_key', flat=True)
        )
        report.created = len(by_key.keys() - existing_keys)
        report.updated = len(existing_keys)
        report.linked, report.unlinked = _count_resolvable(by_key.keys())
        return report

    with transaction.atomic():
        for key, (values, _line) in by_key.items():
            # Only the columns actually present in this CSV are written: a
            # re-import from a narrower export updates what it covers and
            # leaves the rest of the row alone, rather than nulling fields it
            # never claimed to describe. The flip side is that removing a
            # column from the CSV does NOT clear the values already stored for
            # it — correcting a field means supplying it, blank or otherwise.
            entry, created = DocumentMetadata.objects.update_or_create(
                match_key=key,
                defaults={**values, 'source_file': source_file},
            )
            if created:
                report.created += 1
            else:
                report.updated += 1

        report.linked, report.unlinked = relink_entries(match_keys=by_key.keys())

    return report


def relink_entries(match_keys=None):
    """Resolves `document` for catalogue entries that don't have one yet.

    Returns (linked, unlinked) for the entries considered. Restricted to
    `match_keys` when given (the just-imported rows), otherwise runs over the
    whole table — which is what `--relink` does after documents are uploaded
    against an already-loaded catalogue.

    Only ever fills a NULL `document`; never moves one. See
    link_catalogue_entry for why.
    """
    entries = DocumentMetadata.objects.all()
    if match_keys is not None:
        entries = entries.filter(match_key__in=list(match_keys))

    considered = list(entries.values_list('id', 'match_key', 'document_id'))
    unresolved = {key: pk for pk, key, document_id in considered if document_id is None}
    already_linked = sum(1 for _pk, _key, document_id in considered if document_id is not None)

    if not unresolved:
        return already_linked, 0

    # Documents already claimed by some entry are off the table — `document`
    # is a OneToOneField, so assigning one twice would be an IntegrityError.
    claimed = set(
        DocumentMetadata.objects.exclude(document__isnull=True).values_list('document_id', flat=True)
    )

    updates = []
    for document in Document.objects.exclude(id__in=claimed).only('id', 'title', 'content').iterator():
        key = match_key_for_document(document)
        entry_id = unresolved.pop(key, None)
        if entry_id is None:
            continue
        updates.append(DocumentMetadata(id=entry_id, document_id=document.id))
        if not unresolved:
            break

    if updates:
        DocumentMetadata.objects.bulk_update(updates, ['document'], batch_size=5000)

    return already_linked + len(updates), len(unresolved)


def _count_resolvable(match_keys):
    """(would-be-linked, would-stay-unlinked) for a --dry-run, without writing."""
    keys = set(match_keys)
    linked_keys = set(
        DocumentMetadata.objects.filter(match_key__in=keys, document__isnull=False)
        .values_list('match_key', flat=True)
    )
    claimed = set(
        DocumentMetadata.objects.exclude(document__isnull=True).values_list('document_id', flat=True)
    )

    resolvable = set()
    for document in Document.objects.exclude(id__in=claimed).only('id', 'title', 'content').iterator():
        key = match_key_for_document(document)
        if key in keys and key not in linked_keys:
            resolvable.add(key)

    linked = len(linked_keys | resolvable)
    return linked, len(keys) - linked


def facet_values():
    """Distinct values per facet, for populating the filter controls.

    Drawn only from entries that have a linked document: offering
    "University: OU" when no OU file is uploaded would produce a
    guaranteed-empty result. Global rather than scoped to the caller's
    current corpus selection, matching how /api/v1/taxonomy serves reference
    data — a scoped list would reshuffle the checkboxes every time a corpus
    is ticked.
    """
    linked = DocumentMetadata.objects.filter(document__isnull=False)

    values = {}
    for name in FACET_FIELDS:
        found = (
            linked.exclude(**{f'{name}__isnull': True})
            .order_by(name)
            .values_list(name, flat=True)
            .distinct()
        )
        values[name] = [v for v in found if v != '']

    return values


@dataclass(frozen=True)
class FacetSelection:
    """A parsed metadata filter: concrete values per facet, which facets asked
    for "(no value)", and the name-code prefix.

    `values` and `unset` are independent because they compose with OR inside a
    facet — "university is TUFS or has no value" is one checkbox list, not two
    filters. Facets are then AND-ed with each other.
    """

    values: dict = field(default_factory=dict)      # facet -> [value, ...]
    unset: frozenset = frozenset()                  # facets whose "(no value)" is ticked
    name_code: str = ''

    @property
    def is_empty(self):
        return not any(self.values.values()) and not self.unset and not self.name_code


def parse_facet_selection(params):
    """Reads a FacetSelection out of a QueryDict (or any object with
    .getlist/.get), i.e. straight off `request.GET`.

    Each facet is a repeated param whose values may include UNSET. Junk values
    on a numeric facet are dropped rather than 400-ing: the filter is a
    navigation control, and a stale or hand-edited URL should degrade to a
    wider result set, never to an error page.
    """
    values = {}
    unset = set()

    for name in FACET_FIELDS:
        raw = [v.strip() for v in params.getlist(name) if v is not None and v.strip()]
        if UNSET in raw:
            unset.add(name)
            raw = [v for v in raw if v != UNSET]

        if name in NUMERIC_FACETS:
            coerced = []
            for value in raw:
                try:
                    coerced.append(int(value))
                except ValueError:
                    continue
            values[name] = coerced
        else:
            values[name] = raw

    return FacetSelection(
        values=values,
        unset=frozenset(unset),
        name_code=(params.get('name_code') or '').strip(),
    )


def _facet_q(name, values, include_unset, numeric):
    """One facet's Q, or None when the facet isn't filtered at all.

    Returning None rather than an empty Q() is the same convention
    dataplane/repositories/postgres/_error_query.py uses for its taxonomy
    filter: the caller can tell "no predicate for this facet" apart from "a
    predicate that happens to match everything".
    """
    clause = None
    if values:
        clause = Q(**{f'catalogue_entry__{name}__in': values})

    if include_unset:
        # `__isnull=True` on a reverse one-to-one promotes the join to LEFT
        # OUTER, so this single term covers BOTH required cases: a catalogue
        # entry whose field is empty, and a document with no entry at all.
        # That equivalence is the whole reason this feature needs no
        # special-casing per surface — see the plan's LEFT-JOIN decision.
        unset_clause = Q(**{f'catalogue_entry__{name}__isnull': True})
        if not numeric:
            # '' is a legitimate "empty" only for a varchar column; comparing
            # an integer column to '' is a type error in Postgres.
            unset_clause |= Q(**{f'catalogue_entry__{name}': ''})
        clause = unset_clause if clause is None else (clause | unset_clause)

    return clause


def metadata_filter_q(selection):
    """The Q for a FacetSelection — AND across facets, OR within one.

    Returns an empty Q() when nothing is selected, so callers can apply it
    unconditionally: `documents.filter(metadata_filter_q(selection))`.
    """
    if selection.is_empty:
        return Q()

    combined = Q()
    for name in FACET_FIELDS:
        clause = _facet_q(
            name,
            selection.values.get(name),
            name in selection.unset,
            name in NUMERIC_FACETS,
        )
        if clause is not None:
            combined &= clause

    if selection.name_code:
        combined &= Q(catalogue_entry__name_code__istartswith=selection.name_code)

    return combined
