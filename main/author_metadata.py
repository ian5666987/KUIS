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

Three rules run through everything here:

  * **The join key is (university, name_code), never name_code alone.**
    name_code collides across universities in the real catalogue — RAKA is
    both an OU and a CHU student, NAMI both KUIS and APU, five more besides —
    and five of those collisions have survey data under exactly one side. A
    one-sided key hands 8 real files another person's demographics.

  * **Never destroy a source value.** Every normalised field is stored as a
    pair: the raw string verbatim plus the parsed form, NULL when it doesn't
    parse. Three duration cells and two dates genuinely don't parse; the
    client corrects those at source, so the CSV has to stay reconstructible
    from the database.

  * **Never invent one either.** A conflict between a person's surveys
    resolves to NULL plus a report, not to a winner picked by row order. The
    one exception is mother tongue, where the UNION across surveys is taken —
    which invents nothing, because every tongue in the union was asserted by
    some survey of that person.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.db import transaction

from .metadata_catalogue import normalize_header
from .models import Author, AuthorMotherTongue, AuthorSurvey, DocumentMetadata

# CSV header (normalize_header'd) -> what we do with it. Reuses
# metadata_catalogue.normalize_header so a re-export that changes
# capitalisation or spacing doesn't silently drop a column, exactly as the
# catalogue importer guarantees.
AUTHOR_CSV_FIELD_MAP = {
    'name code': 'name_code',
    'date': 'survey_date_raw',
    'university': 'university',
    'age': 'age',
    'gender': 'gender',
    'grade': 'grade',
    'major': 'major',
    'mother tongues': 'mother_tongues_raw',
    'duration of learning indonesian': 'duration_learning_raw',
    'other foreign language (1)': 'other_fl_1',
    'duration of learning other fl (1)': 'duration_fl_1_raw',
    'other foreign language (2)': 'other_fl_2',
    'duration of learning other fl (2)': 'duration_fl_2_raw',
    'indonesian language proficiency test level': 'proficiency_level',
    'test month and year': 'test_month_year',
    'place of staying in indonesia': 'place_of_stay',
    'duration of staying in indonesia': 'duration_of_stay_raw',
    'difficulty level of writing essays in indonesian '
    '(1=very easy~5=very difficult)': 'difficulty_level',
    'reasons': 'reasons',
    'reasons (translated into english)': 'reasons_en',
}

REQUIRED_CSV_HEADERS = ('name code', 'university')

INTEGER_FIELDS = ('age', 'grade', 'difficulty_level')

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

# Mother tongues and places are multi-valued in the source with no single
# separator: the ideographic comma, the katakana middle dot and the ASCII
# comma all appear, sometimes in the same file.
MULTI_VALUE_SPLIT = re.compile(r'[、・,，/&;]+')

# Durations are written as "1 year(s)3 month(s)", "20 month(s)", "2 week(s)",
# "5 day(s)" — summed, so the compound forms work without a second pattern.
_DURATION_TOKEN = re.compile(r'(\d+(?:\.\d+)?)\s*(year|month|week|day)\(s\)')
_MONTHS_PER = {
    'year': Decimal('12'),
    'month': Decimal('1'),
    # Mean weeks/days per month rather than 4 and 30: these feed numeric
    # comparisons, and "1 week" vs "0.25 month" shouldn't disagree by 2 days.
    'week': Decimal('1') / Decimal('4.345'),
    'day': Decimal('1') / Decimal('30.44'),
}

_DATE_WITH_YEAR = re.compile(r'^(\d{4})/(\d{1,2})/(\d{1,2})$')

# Japanese academic year starts in April, so a January 2024 survey describes
# academic year 2023 — which is the year a 2023 filename refers to.
ACADEMIC_YEAR_START_MONTH = 4


def normalize_text(value):
    """NFKC-normalise and collapse whitespace.

    NFKC is what folds the full-width digits the source really contains
    ('１ year(s)', '0.５ month(s)') into ASCII so one numeric pattern handles
    them, and collapsing runs of spaces handles '1  month(s)'.
    """
    text = unicodedata.normalize('NFKC', str(value or ''))
    return ' '.join(text.split()).strip()


def parse_duration_to_months(value):
    """A duration string -> (months as a Decimal or None, note or None).

    Returns a note rather than just a number because a PARTIAL parse is the
    dangerous case. '2、3 year(s)' means "two or three years"; matching only
    the '3 year(s)' and returning 36 would be a silent guess dressed up as
    data. So anything left over after the recognised tokens is inspected:

      * leftover containing a digit -> the value is ambiguous or written in
        another notation ('2カ月', '8ヵ月'). months is None, and the raw
        string is kept for the client to fix at source.
      * leftover with no digit -> a qualifier, in practice always '約'
        ("approximately"). The number is right, so it is kept; the note just
        records that the hedge was dropped.

    Never raises: one bad cell must not cost a whole person's record.
    """
    text = normalize_text(value)
    if not text:
        return None, None

    tokens = _DURATION_TOKEN.findall(text)
    if not tokens:
        return None, 'no recognised duration'

    residue = _DURATION_TOKEN.sub('', text).strip()
    if residue and any(character.isdigit() for character in residue):
        return None, f'ambiguous — {residue!r} left unparsed'

    total = sum(
        (Decimal(amount) * _MONTHS_PER[unit] for amount, unit in tokens),
        Decimal('0'),
    )
    months = total.quantize(Decimal('0.01'))

    if residue:
        return months, f'ignored qualifier {residue!r}'
    return months, None


def parse_survey_date(value):
    """'2024/1/10' -> (date(2024, 1, 10), academic_year).

    Returns (None, None) for a blank cell and for the two rows written
    '11/27' with no year — a bare month/day can't be placed in an academic
    year, and inferring one from the file it sits in would be a guess.
    """
    text = normalize_text(value)
    if not text:
        return None, None

    match = _DATE_WITH_YEAR.match(text)
    if not match:
        return None, None

    year, month, day = (int(g) for g in match.groups())
    try:
        parsed = date(year, month, day)
    except ValueError:
        return None, None

    academic_year = year if month >= ACADEMIC_YEAR_START_MONTH else year - 1
    return parsed, academic_year


def split_mother_tongues(value):
    """A 'Mother tongues' cell -> the set of tongues it names.

    A set, not a list: the only thing downstream cares about is membership,
    and the union across a person's surveys is a set union.
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
    never gets fixed. `errors` block a value from being used, `warnings` don't.
    """

    rows_read: int = 0
    authors_created: int = 0
    authors_updated: int = 0
    surveys_created: int = 0
    surveys_updated: int = 0
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
    unusable; everything else degrades to NULL with the raw string kept, so
    one bad cell never costs a whole person's record.
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

    where = f'{source_file} {university}/{name_code}'

    for name in INTEGER_FIELDS:
        text = normalize_text(values.get(name, ''))
        if not text:
            values[name] = None
            continue
        try:
            values[name] = int(text)
        except ValueError:
            values[name] = None
            report.warnings.append((where, f'{name} {text!r} is not a number -> NULL'))

    survey_date, academic_year = parse_survey_date(values.get('survey_date_raw', ''))
    values['survey_date'] = survey_date
    values['academic_year'] = academic_year
    if values.get('survey_date_raw') and survey_date is None:
        report.warnings.append((
            where,
            f'date {values["survey_date_raw"]!r} has no usable year -> NULL '
            f'(it cannot be placed in an academic year)',
        ))

    for raw_field, months_field in (
        ('duration_learning_raw', 'duration_learning_months'),
        ('duration_fl_1_raw', 'duration_fl_1_months'),
        ('duration_fl_2_raw', 'duration_fl_2_months'),
        ('duration_of_stay_raw', 'duration_of_stay_months'),
    ):
        text = values.get(raw_field, '')
        months, note = parse_duration_to_months(text)
        values[months_field] = months
        if text and note:
            outcome = 'months NULL' if months is None else f'read as {months} month(s)'
            report.warnings.append((
                where, f'{raw_field} {text!r}: {note} -> {outcome}',
            ))

    values['mother_tongues'] = split_mother_tongues(values.get('mother_tongues_raw', ''))
    values['_line'] = line_number
    return values


def derive_invariants(rows, where, report):
    """The person-level facts, from all of that person's survey rows.

    Gender is single-valued, so disagreement resolves to NULL: there is no
    non-arbitrary winner between 'Female' and 'Male', and a filter quietly
    believing the wrong one is worse than it admitting it doesn't know.

    Mother tongue is the UNION, and keeps every value even when the surveys
    disagree. Union invents nothing — each tongue was asserted by some survey
    of this person — and NULLing on disagreement would have cost the Malay
    facet all 21 of its files over one inconsistent row.
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
            f'mother tongue sets differ across surveys: {shown} '
            f'-> imported as the union {{{", ".join(sorted(tongues))}}}',
        ))

    gender_raw = next((r.get('gender', '') for r in rows if r.get('gender')), '')

    return {
        'gender': gender,
        'gender_raw': gender_raw,
        'mother_tongues': tongues,
        'has_conflict': bool(conflicts),
        'conflict_note': '; '.join(conflicts),
    }


def link_authors(universities=None):
    """Resolve DocumentMetadata.author from (university, name_code).

    Mirrors metadata_catalogue.relink_entries, and exists for the same reason:
    the two sides arrive independently, so whichever lands second has to be
    able to resolve the link. Called at the end of an author import, and from
    `load_metadata_catalogue --relink`, so either order converges.

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

    `rows` is any iterable of dicts (a csv.DictReader). Upserts rather than
    replacing the table: Author rows carry resolved document links that a
    delete-and-recreate would throw away.
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
        # Still derive, so conflicts and unparseable values are reported by
        # --dry-run. That is the mode the client's error list comes from.
        for (university, name_code), person_rows in sorted(by_person.items()):
            invariants = derive_invariants(
                person_rows, f'{source_file} {university}/{name_code}', report,
            )
            report.tongues_written += len(invariants['mother_tongues'])
            report.surveys_created += len(person_rows)
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
            # the client removed at source has to disappear here too. Safe to
            # delete because nothing points at these rows — the filter reads
            # them with EXISTS, never by id.
            author.mother_tongues.all().delete()
            AuthorMotherTongue.objects.bulk_create([
                AuthorMotherTongue(
                    author=author,
                    tongue=tongue,
                    tongue_raw=person_rows[0].get('mother_tongues_raw', ''),
                )
                for tongue in sorted(invariants['mother_tongues'])
            ])
            report.tongues_written += len(invariants['mother_tongues'])

            for values in person_rows:
                _, survey_created = AuthorSurvey.objects.update_or_create(
                    author=author,
                    survey_date_raw=values.get('survey_date_raw', ''),
                    defaults={
                        'survey_date': values['survey_date'],
                        'academic_year': values['academic_year'],
                        'age': values['age'],
                        'grade': values['grade'],
                        'major': values.get('major', ''),
                        'mother_tongues_raw': values.get('mother_tongues_raw', ''),
                        'duration_learning_raw': values.get('duration_learning_raw', ''),
                        'duration_learning_months': values['duration_learning_months'],
                        'other_fl_1': values.get('other_fl_1', ''),
                        'duration_fl_1_raw': values.get('duration_fl_1_raw', ''),
                        'duration_fl_1_months': values['duration_fl_1_months'],
                        'other_fl_2': values.get('other_fl_2', ''),
                        'duration_fl_2_raw': values.get('duration_fl_2_raw', ''),
                        'duration_fl_2_months': values['duration_fl_2_months'],
                        'proficiency_level': values.get('proficiency_level', ''),
                        'test_month_year': values.get('test_month_year', ''),
                        'place_of_stay': values.get('place_of_stay', ''),
                        'duration_of_stay_raw': values.get('duration_of_stay_raw', ''),
                        'duration_of_stay_months': values['duration_of_stay_months'],
                        'difficulty_level': values['difficulty_level'],
                        'reasons': values.get('reasons', ''),
                        'reasons_en': values.get('reasons_en', ''),
                        'source_file': source_file,
                    },
                )
                if survey_created:
                    report.surveys_created += 1
                else:
                    report.surveys_updated += 1

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
