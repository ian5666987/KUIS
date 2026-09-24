from django.core.management.base import BaseCommand

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

    def handle(self, *args, **options):
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
        document_ids = list(Document.objects.values_list('id', flat=True))
        total = len(document_ids)

        for i, document_id in enumerate(document_ids, start=1):
            index_document(document_id)
            document = Document.objects.get(id=document_id)

            self.stdout.write(
                f"[{i}/{total}] {document.title}: "
                f"{document.token_count} original tokens, {document.token_count_corrected} corrected tokens"
            )

        self.stdout.write(self.style.SUCCESS(f"Retokenized {total} document(s)."))
