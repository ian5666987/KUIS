from django.core.management.base import BaseCommand

from main.models import Document
from worker.tasks.indexing import aggregate_document


class Command(BaseCommand):
    help = (
        "Finishes indexing for documents stuck at status 'tokenized' — Token "
        "rows complete, but aggregate_document never ran or failed (a lost "
        "Celery message, a worker killed mid-task, or the documents "
        "main/migrations/0018_document_status_tokenized.py flagged as "
        "missing aggregates). Builds DocumentWordFreq, ErrorAnnotation and "
        "DocumentErrorFreq and moves each document to "
        "'ready'. Does NOT rebuild Token rows — that's retokenize. Called "
        "directly (not via .delay()) for the same reason backfill_tier2.py "
        "is: this is already a synchronous, blocking CLI command."
    )

    def handle(self, *args, **options):
        documents = Document.objects.filter(status=Document.STATUS_TOKENIZED).order_by('id')
        document_ids = list(documents.values_list('id', flat=True))
        total = len(document_ids)

        if total == 0:
            self.stdout.write("Nothing to aggregate — no document is waiting at 'tokenized'.")
            return

        failed = 0
        for i, document_id in enumerate(document_ids, start=1):
            try:
                aggregate_document(document_id)
            except Exception:
                # aggregate_document has already recorded the reason on the
                # document; carry on with the rest rather than stopping at the
                # first bad one.
                pass

            document = Document.objects.get(id=document_id)
            if document.status == Document.STATUS_READY:
                self.stdout.write(f"[{i}/{total}] {document.title}: ready")
            else:
                failed += 1
                self.stdout.write(self.style.WARNING(
                    f"[{i}/{total}] {document.title}: still {document.status}"
                    f" ({document.status_reason}) — {document.status_detail}"
                ))

        message = f"Aggregated {total - failed} of {total} document(s)."
        self.stdout.write(self.style.WARNING(message) if failed else self.style.SUCCESS(message))
