"""Person-level author metadata: CSV import, normalisation, document linking.

Feature context: docs/author-metadata-plan.md.

A sibling of main/metadata_catalogue.py, not part of it, because the two read
CSVs at different GRAINS and neither one's rules make sense for the other:

  * metadata/*.csv is the catalogue — one row per FILE, keyed on a filename.
  * metadata/authors/*.csv is the survey data — one row per PERSON per survey
    OCCASION, keyed on (university, name code).

Keeping them apart is also what unbroke the catalogue loader. Its
`metadata/*.csv` glob is non-recursive and it hard-fails any CSV without a
`file name` column, so while these three files sat in metadata/ a bare
`load_metadata_catalogue` aborted before importing anything (KUIS.csv sorts
first). metadata/authors/ is load-bearing, not cosmetic — see
main/tests.py::test_only_catalogue_schema_csvs_live_directly_in_metadata.

**Only two traits are imported: gender and mother tongue.** The source CSVs
carry 20 columns; the other sixteen are read past and dropped. Two reasons,
and the first is the decisive one:

  * Nothing reads them. Storing a column no query touches is just a second
    copy of the CSV that can drift from the first.
  * Most of them *cannot* be attached to a file anyway. A person is surveyed
    twice per academic year and a filename records only year and grade, never
    which survey, so age, learning duration and the difficulty self-rating
    have no single honest value per file. `Major` is worse: KUIS and OU record
    a language, TUFS a faculty.

The CSVs are the authority and are tracked in git, so if the client later
settles a rule for one of those traits, re-importing is the entire cost of
getting it back. That is why dropping them is cheap and keeping them unread
was not.

Three rules run through what remains:

  * **The join key is (university, name_code), never name_code alone.**
    name_code collides across universities in the real catalogue — RAKA is
    both an OU and a CHU student, NAMI both KUIS and APU, five more besides —
    and five of those collisions have survey data under exactly one side. A
    one-sided key hands 8 real files another person's demographics.

  * **Never destroy a source value.** What is imported is stored raw as well
    as normalised (gender_raw, tongue_raw), so a normalisation bug stays
    traceable to what the CSV actually said.

  * **Never invent one either.** A conflict between a person's survey rows
    resolves to NULL plus a report, not to a winner picked by row order. The
    one exception is mother tongue, where the UNION across rows is taken —
    which invents nothing, because every tongue in the union was asserted by
    some row for that person.
"""

import re
import unicodedata
from dataclasses import dataclass, field

from django.db import transaction

from .metadata_catalogue import normalize_header
from .models import Author, AuthorMotherTongue, DocumentMetadata

# CSV header (normalize_header'd) -> what we keep. Reuses
# metadata_catalogue.normalize_header so a re-export that changes
# capitalisation or spacing doesn't silently drop a column, exactly as the
# catalogue importer guarantees.
#
# Four entries out of the source's twenty columns. Unmapped headers are read
# past in silence, which is the same thing import_catalogue_rows does — a
# column the model has no home for is not an error.
AUTHOR_CSV_FIELD_MAP = {
    'name code': 'name_code',
    'university': 'university',
    'gender': 'gender',
    'mother tongues': 'mother_tongues_raw',
}

REQUIRED_CSV_HEADERS = ('name code', 'university')

# The survey CSVs spell Osaka out; the catalogue uses the filename prefix.
# An unmapped university is an ERROR rather than a pass-through: it would
# create an author that can never link to a file, and silently contribute
# nothing to the facets — a failure with no symptom.
UNIVERSITY_ALIASES = {
    'kuis': 'KUIS',
    'tufs': 'TUFS',
    'osaka': 'OU',
    'ou': 'OU',
}

# Mother tongues are multi-valued in the source with no single separator: the
# ideographic comma, the katakana middle dot and the ASCII comma all appear,
# sometimes in the same file.
MULTI_VALUE_SPLIT = re.compile(r'[、・,，/&;]+')


def normalize_text(value):
    """NFKC-normalise and collapse whitespace.

    NFKC folds the full-width characters the source really contains into
    ASCII, and collapsing runs of spaces handles the stray double spaces a
    hand-edited spreadsheet leaves behind.
    """
    text = unicodedata.normalize('NFKC', str(value or ''))
    return ' '.join(text.split()).strip()


def split_mother_tongues(value):
    """A 'Mother tongues' cell -> the set of tongues it names.

    A set, not a list: the only thing downstream cares about is membership,
    and the union across a person's rows is a set union.
    """
    text = normalize_text(value)
    if not text:
        return set()
    return {part.strip() for part in MULTI_VALUE_SPLIT.split(text) if part.strip()}


@dataclass
class AuthorImportReport:
    """What one import pass did, and everything the client has to fix.

    The validation report IS the deliverable here, not a side effect: the
    client owns the CSVs and corrects them at source
    (docs/author-metadata-plan.md), so an inconsistency that never gets named
    never gets fixed. `errors` mean a value could not be used; `warnings` mean
    it was imported with a caveat.

    Scoped to the two traits actually imported. A malformed duration or a
    year-less date is no longer reported, because nothing reads those columns
    — reporting them would be asking the client to fix data the system has no
    opinion about.
    """

    rows_read: int = 0
    authors_created: int = 0
    authors_updated: int = 0
    tongues_written: int = 0
    linked: int = 0

    errors: list = field(default_factory=list)       # (where, message)
    warnings: list = field(default_factory=list)     # (where, message)
    skipped: list = field(default_factory=list)      # (line number, reason)
    unlinked_authors: list = field(default_factory=list)   # 'UNI/CODE'

    @property
    def authors_total(self):
        return self.authors_created + self.authors_updated

    @property
    def has_errors(self):
        return bool(self.errors)


def _coerce_row(raw, line_number, source_file, report):
    """One CSV row -> a field dict, or None when it can't be used at all.

    Only a missing name code or an unrecognised university makes a row
    unusable. A blank gender or mother tongue is a legitimate "not recorded"
    and imports as such.
    """
    values = {}
    for header, value in (raw or {}).items():
        target = AUTHOR_CSV_FIELD_MAP.get(normalize_header(header))
        if target is None:
            continue
        # .strip() also removes the trailing \r that CRLF line endings leave
        # on the last column when a reader doesn't eat them — the exact bug
        # that put 'KOMSHI\r' in the catalogue's name codes.
        values[target] = (value or '').strip()

    name_code = values.get('name_code', '')
    if not name_code:
        report.skipped.append((line_number, 'no Name code'))
        return None

    raw_university = values.get('university', '')
    university = UNIVERSITY_ALIASES.get(normalize_text(raw_university).lower())
    if not university:
        report.errors.append((
            f'{source_file}:L{line_number} {name_code}',
            f'unrecognised university {raw_university!r} — add it to '
            f'UNIVERSITY_ALIASES or fix the CSV; the row is skipped because '
            f'an author with an unknown university can never link to a file',
        ))
        return None
    values['university'] = university

    values['mother_tongues'] = split_mother_tongues(values.get('mother_tongues_raw', ''))
    values['_line'] = line_number
    return values


def derive_invariants(rows, where, report):
    """The person-level facts, from all of that person's rows.

    Gender is single-valued, so disagreement resolves to NULL: there is no
    non-arbitrary winner between 'Female' and 'Male', and a filter quietly
    believing the wrong one is worse than it admitting it doesn't know.

    Mother tongue is the UNION, and keeps every value even when the rows
    disagree. Union invents nothing — each tongue was asserted by some row for
    this person — and NULLing on disagreement would have cost the Malay facet
    all 21 of its files over one inconsistent row.
    """
    genders = {r['gender'] for r in rows if r.get('gender')}
    gender = None
    conflicts = []

    if len(genders) == 1:
        gender = next(iter(genders))
    elif len(genders) > 1:
        conflicts.append(f'gender: {" / ".join(sorted(genders))} -> NULL')
        report.errors.append((
            where, f'gender conflict: {" / ".join(sorted(genders))} -> gender NULL',
        ))

    tongue_sets = {frozenset(r['mother_tongues']) for r in rows if r['mother_tongues']}
    tongues = set().union(*tongue_sets) if tongue_sets else set()

    if len(tongue_sets) > 1:
        shown = ' / '.join(
            '{' + ', '.join(sorted(s)) + '}' for s in sorted(tongue_sets, key=sorted)
        )
        conflicts.append(f'mother tongue sets differ: {shown} -> union')
        report.warnings.append((
            where,
            f'mother tongue sets differ across rows: {shown} '
            f'-> imported as the union {{{", ".join(sorted(tongues))}}}',
        ))

    gender_raw = next((r.get('gender', '') for r in rows if r.get('gender')), '')
    tongue_raw = next(
        (r.get('mother_tongues_raw', '') for r in rows if r.get('mother_tongues_raw')), '',
    )

    return {
        'gender': gender,
        'gender_raw': gender_raw,
        'mother_tongues': tongues,
        'tongue_raw': tongue_raw,
        'has_conflict': bool(conflicts),
        'conflict_note': '; '.join(conflicts),
    }


def link_authors(universities=None):
    """Resolve DocumentMetadata.author from (university, name_code).

    Mirrors metadata_catalogue.relink_entries, and exists for the same reason:
    the two sides arrive independently, so whichever lands second has to be
    able to resolve the link. Called at the end of an author import, and from
    `load_author_metadata --relink`, so either order converges.

    Matching on BOTH columns is the whole point — see the module docstring.
    `universities` narrows the scan when only some files were imported.
    """
    authors = {
        (a.university, a.name_code): a.id
        for a in Author.objects.all().only('id', 'university', 'name_code')
    }
    if not authors:
        return 0

    entries = DocumentMetadata.objects.filter(author__isnull=True)
    if universities:
        entries = entries.filter(university__in=list(universities))

    changed = []
    for entry in entries.only('id', 'university', 'name_code'):
        author_id = authors.get((entry.university, entry.name_code))
        if author_id is not None:
            entry.author_id = author_id
            changed.append(entry)

    if changed:
        DocumentMetadata.objects.bulk_update(changed, ['author'], batch_size=500)

    return len(changed)


def import_author_rows(rows, source_file, dry_run=False):
    """Import one CSV's rows. Split out from the management command so an
    admin upload endpoint can reuse it, exactly as import_catalogue_rows is.

    `rows` is any iterable of dicts (a csv.DictReader). Several rows describe
    the same person — one per survey occasion — so they are grouped first and
    collapsed into one Author. Upserts rather than replacing the table:
    Author rows carry resolved document links that a delete-and-recreate
    would throw away.
    """
    report = AuthorImportReport()

    by_person = {}
    for offset, raw in enumerate(rows):
        # +2: one for the header line, one for 1-based line numbers, so the
        # number printed is the one the client sees in their spreadsheet.
        line_number = offset + 2
        report.rows_read += 1
        values = _coerce_row(raw, line_number, source_file, report)
        if values is None:
            continue
        by_person.setdefault((values['university'], values['name_code']), []).append(values)

    if dry_run:
        # Still derive, so conflicts are reported by --dry-run. That is the
        # mode the client's error list comes from.
        for (university, name_code), person_rows in sorted(by_person.items()):
            invariants = derive_invariants(
                person_rows, f'{source_file} {university}/{name_code}', report,
            )
            report.tongues_written += len(invariants['mother_tongues'])
        report.authors_created = len(by_person)
        return report

    with transaction.atomic():
        for (university, name_code), person_rows in sorted(by_person.items()):
            where = f'{source_file} {university}/{name_code}'
            invariants = derive_invariants(person_rows, where, report)

            author, created = Author.objects.update_or_create(
                university=university,
                name_code=name_code,
                defaults={
                    'gender': invariants['gender'],
                    'gender_raw': invariants['gender_raw'],
                    'has_conflict': invariants['has_conflict'],
                    'conflict_note': invariants['conflict_note'],
                    'source_file': source_file,
                },
            )
            if created:
                report.authors_created += 1
            else:
                report.authors_updated += 1

            # Replace rather than merge: the CSV is the authority, so a tongue
            # the client removes at source has to disappear here too. Safe to
            # delete because nothing points at these rows — the filter reads
            # them with EXISTS, never by id.
            author.mother_tongues.all().delete()
            AuthorMotherTongue.objects.bulk_create([
                AuthorMotherTongue(
                    author=author,
                    tongue=tongue,
                    tongue_raw=invariants['tongue_raw'],
                )
                for tongue in sorted(invariants['mother_tongues'])
            ])
            report.tongues_written += len(invariants['mother_tongues'])

        report.linked = link_authors(
            universities={university for university, _ in by_person},
        )

    report.unlinked_authors = [
        f'{a.university}/{a.name_code}'
        for a in Author.objects.filter(documents__isnull=True).only('university', 'name_code')
    ]

    return report


def catalogue_codes_without_authors():
    """(university, name_code) pairs that have catalogue files but no author.

    Reported so coverage gaps are visible: KUIS/WAMO has files and no survey
    row, and four whole universities (APU, CHU, SFC, SOP) have no survey data
    at all — which is why ~53% of the corpus is legitimately author-less.
    """
    missing = (
        DocumentMetadata.objects
        .filter(author__isnull=True)
        .values_list('university', 'name_code')
        .distinct()
        .order_by('university', 'name_code')
    )
    return list(missing)
