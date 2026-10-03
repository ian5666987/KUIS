from django.core.management.base import BaseCommand

from main.models import Document
from worker.tasks.aggregates import compute_document_ngrams, compute_document_word_freq


class Command(BaseCommand):
    help = (
        "Computes DocumentWordFreq/DocumentNgram (Tier 2) for documents that "
        "already have Token/WordType data but no aggregates yet — the "
        "one-off backfill new uploads get automatically (via "
        "worker/tasks/indexing.py::index_document, chained after "
        "tokenization) but existing documents from before Phase 5 don't. "
        "Unlike retokenize, this does NOT rebuild Token rows — it only "
        "(re)computes the aggregates from whatever tokens already exist. "
        "Called directly (not via .delay()) for the same reason "
        "retokenize.py is: this is already a synchronous, blocking CLI "
        "command."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--all',
            action='store_true',
            help='Recompute for every document, not just ones missing aggregates.',
        )

    def handle(self, *args, **options):
        documents = Document.objects.all()
        if not options['all']:
            documents = documents.filter(word_freqs__isnull=True).distinct()

        document_ids = list(documents.values_list('id', flat=True))
        total = len(document_ids)

        if total == 0:
            self.stdout.write('Nothing to backfill — every document already has Tier-2 aggregates.')
            return

        for i, document_id in enumerate(document_ids, start=1):
            word_freq_count = compute_document_word_freq(document_id)
            ngram_count = compute_document_ngrams(document_id)
            document = Document.objects.get(id=document_id)
            self.stdout.write(
                f"[{i}/{total}] {document.title}: "
                f"{word_freq_count} word-freq rows, {ngram_count} n-gram rows"
            )

        self.stdout.write(self.style.SUCCESS(f"Backfilled Tier-2 aggregates for {total} document(s)."))
