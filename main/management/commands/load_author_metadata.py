import csv

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from main.author_metadata import (
    REQUIRED_CSV_HEADERS,
    AuthorImportReport,
    catalogue_codes_without_authors,
    import_author_rows,
    link_authors,
)
from main.metadata_catalogue import normalize_header

# A subdirectory, not metadata/ itself. load_metadata_catalogue globs
# metadata/*.csv non-recursively and aborts on any CSV without a `file name`
# column, so these two schemas have to live in separate directories or the
# author files take the catalogue import down with them.
AUTHORS_DIR = settings.BASE_DIR / 'metadata' / 'authors'


class Command(BaseCommand):
    help = (
        "Loads person-level author metadata from metadata/authors/*.csv into "
        "Author / AuthorMotherTongue / AuthorSurvey, and links each author to "
        "its catalogue rows on (university, name code). "
        "Idempotent and safe to rerun, like load_metadata_catalogue and "
        "load_error_taxonomy: survey data gets corrected at source "
        "independently of the schema, so it is not baked into migration "
        "history. UPSERTS rather than replacing the table, because Author "
        "rows carry resolved document links. "
        "Linking is on university AND name code together — name codes collide "
        "across universities, so a one-sided match mis-assigns real files. "
        "Run --dry-run first: the validation report is what the data owner "
        "needs in order to fix the CSVs."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--file',
            help='One CSV to load, relative to metadata/authors/ (or an absolute path).',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Validate and report without writing anything.',
        )
        parser.add_argument(
            '--relink',
            action='store_true',
            help='Only re-resolve author links for existing rows; import nothing.',
        )

    def handle(self, *args, **options):
        if options['relink']:
            self._relink()
            return

        paths = self._resolve_paths(options.get('file'))
        if not paths:
            raise CommandError(
                f"No CSVs found in {AUTHORS_DIR}. Put the author CSVs there, "
                f"or pass --file."
            )

        dry_run = options['dry_run']
        total = AuthorImportReport()

        for path in paths:
            report = self._load_one(path, dry_run)
            self._report(path, report, dry_run)

            total.rows_read += report.rows_read
            total.authors_created += report.authors_created
            total.authors_updated += report.authors_updated
            total.surveys_created += report.surveys_created
            total.surveys_updated += report.surveys_updated
            total.tongues_written += report.tongues_written
            total.linked += report.linked
            total.errors.extend(report.errors)
            total.warnings.extend(report.warnings)
            total.skipped.extend(report.skipped)

        if len(paths) > 1:
            self._summary(total, dry_run)

        self._coverage(dry_run)

        if total.has_errors:
            # Non-zero exit so CI notices a regression in the source data, and
            # so a scripted deploy doesn't march past a conflict.
            raise CommandError(
                f"{len(total.errors)} data error(s) above need fixing in the "
                f"source CSVs. Everything else was "
                f"{'validated' if dry_run else 'imported'}."
            )

    def _resolve_paths(self, file_option):
        from pathlib import Path

        if file_option:
            path = Path(file_option)
            if not path.is_absolute():
                path = AUTHORS_DIR / path
            if not path.is_file():
                raise CommandError(f"No such file: {file_option}")
            return [path]

        if not AUTHORS_DIR.is_dir():
            raise CommandError(
                f"{AUTHORS_DIR} doesn't exist. Put the author CSVs there, "
                f"or pass --file."
            )

        return sorted(AUTHORS_DIR.glob('*.csv'))

    def _load_one(self, path, dry_run):
        try:
            # newline='' and utf-8-sig for the same two reasons the catalogue
            # loader uses them: the source is CRLF, and an Excel re-export
            # routinely adds a BOM.
            with open(path, newline='', encoding='utf-8-sig') as handle:
                reader = csv.DictReader(handle)
                headers = {normalize_header(h) for h in (reader.fieldnames or [])}
                missing = [h for h in REQUIRED_CSV_HEADERS if h not in headers]
                if missing:
                    raise CommandError(
                        f"{path.name} is missing required column(s): "
                        f"{', '.join(missing)}. "
                        f"Found: {', '.join(reader.fieldnames or ['<empty file>'])}"
                    )

                return import_author_rows(
                    reader, source_file=path.name, dry_run=dry_run,
                )
        except OSError as exc:
            raise CommandError(f"Couldn't read {path}: {exc}")
        except UnicodeDecodeError as exc:
            raise CommandError(
                f"{path.name} isn't UTF-8 encoded ({exc}). Re-export it as UTF-8."
            )

    def _report(self, path, report, dry_run):
        self.stdout.write(self.style.MIGRATE_HEADING(f'\n{path.name}'))
        verb = 'would import' if dry_run else 'imported'
        self.stdout.write(
            f'  {verb}: {report.authors_total} author(s), '
            f'{report.surveys_created + report.surveys_updated} survey(s), '
            f'{report.tongues_written} mother tongue(s) '
            f'from {report.rows_read} row(s)'
        )
        if not dry_run:
            self.stdout.write(f'  linked: {report.linked} catalogue file(s)')

        if report.skipped:
            self.stdout.write(self.style.WARNING('  SKIPPED (row unusable)'))
            for line_number, reason in report.skipped:
                self.stdout.write(f'    L{line_number}: {reason}')

        if report.errors:
            self.stdout.write(self.style.ERROR('  ERRORS (value not usable)'))
            for where, message in report.errors:
                self.stdout.write(f'    {where}: {message}')

        if report.warnings:
            self.stdout.write(self.style.WARNING('  WARNINGS (imported anyway)'))
            for where, message in report.warnings:
                self.stdout.write(f'    {where}: {message}')

    def _summary(self, total, dry_run):
        verb = 'would import' if dry_run else 'imported'
        self.stdout.write(self.style.MIGRATE_HEADING('\nAll files'))
        self.stdout.write(
            f'  {verb}: {total.authors_total} author(s), '
            f'{total.surveys_created + total.surveys_updated} survey(s) '
            f'from {total.rows_read} row(s)'
        )
        if not dry_run:
            self.stdout.write(f'  linked: {total.linked} catalogue file(s)')
        self.stdout.write(
            f'  {len(total.errors)} error(s), {len(total.warnings)} warning(s)'
        )

    def _coverage(self, dry_run):
        """Which catalogue files still have no author, and why.

        Printed every run because it is the number that decides what the
        filter's "(not specified)" option actually covers — a silent 53% is
        the failure mode this feature has to avoid.
        """
        if dry_run:
            return

        from main.models import Author, DocumentMetadata

        total = DocumentMetadata.objects.count()
        if not total:
            self.stdout.write(
                '\n  catalogue is empty — run load_metadata_catalogue to link files.'
            )
            return

        linked = DocumentMetadata.objects.filter(author__isnull=False).count()
        self.stdout.write(self.style.MIGRATE_HEADING('\nCoverage'))
        self.stdout.write(
            f'  {linked} of {total} catalogue file(s) have an author '
            f'({100 * linked // total}%); the rest are "(not specified)" '
            f'in the author filters.'
        )

        missing = catalogue_codes_without_authors()
        if missing:
            by_university = {}
            for university, name_code in missing:
                by_university.setdefault(university, []).append(name_code or '<blank>')
            self.stdout.write('  catalogue name codes with no survey row:')
            for university in sorted(by_university):
                codes = by_university[university]
                shown = ', '.join(codes[:8])
                more = f' (+{len(codes) - 8} more)' if len(codes) > 8 else ''
                self.stdout.write(f'    {university}: {len(codes)} -> {shown}{more}')

        orphans = Author.objects.filter(documents__isnull=True).count()
        if orphans:
            self.stdout.write(
                f'  {orphans} author(s) have no catalogue file '
                f'(surveys run ahead of the catalogue).'
            )

    def _relink(self):
        from main.models import Author

        if not Author.objects.exists():
            self.stdout.write('No authors imported yet — nothing to relink.')
            return

        linked = link_authors()
        self.stdout.write(f'Linked {linked} catalogue file(s) to an author.')
        self._coverage(dry_run=False)
