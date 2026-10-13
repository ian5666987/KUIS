from django.core.management.base import BaseCommand

from main.corpus_parsing import extract_word_streams
from main.models import Document
from worker.tasks.indexing import index_document


class Command(BaseCommand):
    help = (
        "Regenerates Token rows for every Document from its current content, "
        "using the XML-aware tokenizer (main.corpus_parsing), and dispatches "
        "the Tier-2 aggregate recomputation (DocumentWordFreq/DocumentNgram) "
        "that would otherwise go stale against the rebuilt tokens. Token "
        "rebuilding happens synchronously as this command runs; the aggregate "
        "recomputation is dispatched via Celery like any other indexing job, "
        "so a worker process must be running (or CELERY_TASK_ALWAYS_EAGER=1 "
        "set) for it to actually complete. Use this after changing "
        "tokenization logic to fix documents that were tokenized before the "
        "change."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--unindexed-only',
            action='store_true',
            help=(
                "Only process documents the indexing task never completed "
                "against (tokenizer_version IS NULL) — the recovery path for "
                "uploads that landed while no Celery worker was consuming the "
                "queue, which main/migrations/0016_document_status_honesty.py "
                "flags as failed/never_indexed. Avoids re-tokenizing the whole "
                "corpus just to repair a handful of rows."
            ),
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help=(
                "Report before/after token-count deltas from the current "
                "tokenizer (e.g. the nested-segment fix in "
                "docs/error-analytics-plan.md) without writing anything — "
                "review this against corpus_db before running for real."
            ),
        )

    def handle(self, *args, **options):
        if options['dry_run']:
            self._dry_run()
            return

        # Delegates to the same task the worker runs for a new upload
        # (architecture plan §5) rather than duplicating its tokenize ->
        # hash -> aggregate sequence here — this command used to do that
        # itself and NOT recompute DocumentWordFreq/DocumentNgram
        # afterward, silently leaving them stale against the rebuilt
        # tokens (exactly the kind of drift optimisation_plan.md's
        # content-hash section warns about). Called directly rather than
        # via .delay() — this is already a synchronous, blocking CLI
        # command; there's no reason to introduce async dispatch (and a
        # dependency on a separately-running worker process) just to
        # retokenize from the command line.
        documents = Document.objects.all()
        if options['unindexed_only']:
            documents = documents.filter(tokenizer_version__isnull=True)

        document_ids = list(documents.values_list('id', flat=True))
        total = len(document_ids)

        if not total:
            self.stdout.write(self.style.SUCCESS('Nothing to retokenize.'))
            return

        for i, document_id in enumerate(document_ids, start=1):
            index_document(document_id)
            document = Document.objects.get(id=document_id)

            if document.status == Document.STATUS_FAILED:
                # index_document records why rather than raising for a content
                # problem, so report it here instead of printing "0 tokens" and
                # letting it read as success.
                self.stdout.write(self.style.WARNING(
                    f"[{i}/{total}] {document.title}: FAILED "
                    f"({document.status_reason}) — {document.status_detail}"
                ))
            else:
                self.stdout.write(
                    f"[{i}/{total}] {document.title}: "
                    f"{document.token_count} original tokens, {document.token_count_corrected} corrected tokens"
                )

        self.stdout.write(self.style.SUCCESS(f"Retokenized {total} document(s)."))

    def _dry_run(self):
        changed = 0
        for document in Document.objects.all():
            new_original, new_corrected = extract_word_streams(document.content)
            delta_original = len(new_original) - document.token_count
            delta_corrected = len(new_corrected) - document.token_count_corrected
            if delta_original or delta_corrected:
                changed += 1
            self.stdout.write(
                f"{document.title}: original {document.token_count} -> {len(new_original)} "
                f"({delta_original:+d}), corrected {document.token_count_corrected} -> "
                f"{len(new_corrected)} ({delta_corrected:+d})"
            )
        self.stdout.write(self.style.WARNING(
            f"Dry run only — no Token rows changed. {changed} document(s) would change."
        ))
