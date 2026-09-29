from django.core.management.base import BaseCommand

from main.models import Document
from worker.tasks.error_annotations import compute_document_error_freq, extract_document_error_annotations


class Command(BaseCommand):
    help = (
        "Extracts ErrorAnnotation/DocumentErrorFreq for documents that "
        "already have Token data but no error annotations yet — new "
        "uploads get this automatically (via "
        "worker/tasks/indexing.py::index_document, chained after "
        "tokenization) but existing documents from before this feature "
        "shipped don't. Does NOT rebuild Token rows — run retokenize "
        "first if the nested-segment tokenizer fix hasn't been applied to "
        "this document yet (docs/error-analytics-plan.md). Called "
        "directly (not via .delay()) for the same reason backfill_tier2.py "
        "is: this is already a synchronous, blocking CLI command."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--all',
            action='store_true',
            help='Recompute for every document, not just ones missing error annotations.',
        )

    def handle(self, *args, **options):
        documents = Document.objects.all()
        if not options['all']:
            documents = documents.filter(error_annotations__isnull=True).distinct()

        document_ids = list(documents.values_list('id', flat=True))
        total = len(document_ids)

        if total == 0:
            self.stdout.write('Nothing to backfill — every document already has error annotations.')
            return

        for i, document_id in enumerate(document_ids, start=1):
            annotation_count = extract_document_error_annotations(document_id)
            freq_count = compute_document_error_freq(document_id)
            document = Document.objects.get(id=document_id)
            self.stdout.write(
                f"[{i}/{total}] {document.title}: "
                f"{annotation_count} error annotation(s), {freq_count} error-freq row(s)"
            )

        self.stdout.write(self.style.SUCCESS(f"Backfilled error annotations for {total} document(s)."))
