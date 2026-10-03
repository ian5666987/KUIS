import csv

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from main.metadata_catalogue import (
    REQUIRED_CSV_HEADERS,
    ImportReport,
    normalize_header,
    import_catalogue_rows,
    relink_entries,
)

METADATA_DIR = settings.BASE_DIR / 'metadata'


class Command(BaseCommand):
    help = (
        "Loads the document metadata catalogue from metadata/*.csv into "
        "DocumentMetadata, and links each row to its uploaded Document. "
        "Idempotent and safe to run repeatedly (like load_error_taxonomy and "
        "seed.py) — catalogue content gets corrected and extended "
        "independently of the schema, so it is not baked into migration "
        "history. "
        "Unlike load_error_taxonomy this UPSERTS rather than replacing the "
        "table: these rows carry resolved `document` foreign keys that a "
        "delete-and-recreate would throw away. "
        "Matching is on the document's own <header><textfile> basename, "
        "falling back to its title, with one extension stripped and "
        "lowercased — never on the raw filename, since the catalogue records "
        "'.txt' for files that are uploaded as '.xml'. "
        "Rows whose file has not been uploaded yet are kept and left "
        "unlinked; run with --relink after uploading to resolve them. "
        "A filename appearing more than once with differing data is resolved "
        "last-row-wins and reported. See docs/metadata-catalogue-plan.md."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--file',
            help='Import this one CSV instead of every metadata/*.csv. '
                 'Relative paths resolve against the metadata/ directory.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report what would be imported and linked, write nothing.',
        )
        parser.add_argument(
            '--relink',
            action='store_true',
            help="Only re-resolve document links for catalogue rows already in "
                 "the database; don't read any CSV. Use after uploading files "
                 "against an already-loaded catalogue.",
        )

    def handle(self, *args, **options):
        if options['relink']:
            self._relink(dry_run=options['dry_run'])
            return

        paths = self._resolve_paths(options['file'])
        if not paths:
            self.stdout.write(
                f"Nothing to load — no CSV files in "
                f"{METADATA_DIR.relative_to(settings.BASE_DIR)}/."
            )
            return

        totals = ImportReport()

        for path in paths:
            report = self._load_one(path, dry_run=options['dry_run'])
            self._report(path, report)

            totals.created += report.created
            totals.updated += report.updated
            totals.rows_read += report.rows_read
            totals.linked += report.linked
            totals.unlinked += report.unlinked

        if len(paths) > 1:
            self.stdout.write(
                f"\nTotal: {totals.total} entr{'y' if totals.total == 1 else 'ies'} "
                f"from {len(paths)} file(s) — {totals.linked} linked to a document, "
                f"{totals.unlinked} awaiting upload."
            )

        if options['dry_run']:
            self.stdout.write(self.style.WARNING('\nDry run — nothing was written.'))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"\nCatalogue loaded: {totals.created} created, {totals.updated} updated."
            ))

    def _resolve_paths(self, file_option):
        if file_option:
            path = METADATA_DIR / file_option
            if not path.exists():
                # Allow an absolute or cwd-relative path too, so the command is
                # usable against a catalogue that isn't in the repo yet.
                from pathlib import Path
                path = Path(file_option)
            if not path.exists():
                raise CommandError(f"No such file: {file_option}")
            return [path]

        if not METADATA_DIR.is_dir():
            raise CommandError(
                f"{METADATA_DIR} doesn't exist. Put the catalogue CSVs there, "
                f"or pass --file."
            )

        return sorted(METADATA_DIR.glob('*.csv'))

    def _load_one(self, path, dry_run):
        try:
            # newline='' is required by the csv module and is what keeps the
            # catalogue's CRLF line endings from leaving a trailing \r on the
            # last column of every row. utf-8-sig tolerates a BOM, which a
            # re-export from Excel routinely adds.
            with open(path, newline='', encoding='utf-8-sig') as handle:
                reader = csv.DictReader(handle)
                headers = {normalize_header(h) for h in (reader.fieldnames or [])}
                missing = [h for h in REQUIRED_CSV_HEADERS if h not in headers]
                if missing:
                    raise CommandError(
                        f"{path.name} is missing required column(s): {', '.join(missing)}. "
                        f"Found: {', '.join(reader.fieldnames or ['<empty file>'])}"
                    )

                return import_catalogue_rows(reader, source_file=path.name, dry_run=dry_run)
        except OSError as exc:
            raise CommandError(f"Couldn't read {path}: {exc}")
        except UnicodeDecodeError as exc:
            raise CommandError(
                f"{path.name} isn't UTF-8 encoded ({exc}). Re-export it as UTF-8."
            )

    def _report(self, path, report):
        self.stdout.write(
            f"{path.name}: {report.rows_read} row(s) read -> {report.total} entr"
            f"{'y' if report.total == 1 else 'ies'} "
            f"({report.created} created, {report.updated} updated); "
            f"{report.linked} linked to a document, {report.unlinked} awaiting upload."
        )

        if report.conflicts:
            self.stdout.write(self.style.WARNING(
                f"  {len(report.conflicts)} filename(s) appear more than once with "
                f"DIFFERING data — the last row won:"
            ))
            for name, lines in sorted(report.conflicts.items()):
                self.stdout.write(self.style.WARNING(
                    f"    {name} (lines {', '.join(str(n) for n in lines)})"
                ))

        repeats = {
            name: lines for name, lines in report.duplicates.items()
            if name not in report.conflicts
        }
        if repeats:
            self.stdout.write(
                f"  {len(repeats)} filename(s) repeated with identical data (harmless)."
            )

        if report.skipped:
            self.stdout.write(self.style.WARNING(
                f"  {len(report.skipped)} value(s) skipped:"
            ))
            for line_number, reason in report.skipped[:20]:
                self.stdout.write(self.style.WARNING(f"    line {line_number}: {reason}"))
            if len(report.skipped) > 20:
                self.stdout.write(self.style.WARNING(
                    f"    ... and {len(report.skipped) - 20} more"
                ))

    def _relink(self, dry_run):
        from main.models import DocumentMetadata

        total = DocumentMetadata.objects.count()
        if total == 0:
            self.stdout.write(
                'Nothing to relink — the catalogue is empty. Run without --relink first.'
            )
            return

        if dry_run:
            unlinked = DocumentMetadata.objects.filter(document__isnull=True).count()
            self.stdout.write(
                f"{total} catalogue entr{'y' if total == 1 else 'ies'}, "
                f"{unlinked} currently unlinked."
            )
            self.stdout.write(self.style.WARNING('Dry run — nothing was written.'))
            return

        linked, unlinked = relink_entries()
        self.stdout.write(self.style.SUCCESS(
            f"Relinked: {linked} of {total} catalogue entries now point at a "
            f"document, {unlinked} still awaiting upload."
        ))
