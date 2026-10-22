from collections import Counter

from django.core.management.base import BaseCommand

from main.corpus_parsing import diagnose_content, extract_words
from main.models import Document
from worker.tasks.indexing import index_document
from worker.tasks.maintenance import SCAN_CHUNK_SIZE, find_stale_documents


class Command(BaseCommand):
    help = (
        "Regenerates Token rows for every Document whose tokens are stale — "
        "never built (including uploads no worker picked up), built by an older "
        "TOKENIZER_VERSION, or built from content the document no longer holds, "
        "e.g. after an edit in the admin (worker/tasks/maintenance.py) — and "
        "dispatches aggregate_document to rebuild everything derived from them "
        "(DocumentWordFreq/ErrorAnnotation/DocumentErrorFreq), "
        "which would otherwise go stale against the rebuilt tokens. Token "
        "rebuilding happens synchronously as this command runs, leaving each "
        "document 'tokenized'; aggregate_document is dispatched via Celery "
        "like any other indexing job, so a worker process must be running "
        "(or CELERY_TASK_ALWAYS_EAGER=1 set) for documents to reach 'ready' "
        "— otherwise finish them with aggregate_documents. After changing "
        "tokenization logic, bump TOKENIZER_VERSION in worker/tasks/indexing.py "
        "and every document indexed before the change counts as stale; --all "
        "rebuilds every document regardless."
    )

    def add_arguments(self, parser):
        selection = parser.add_mutually_exclusive_group()
        selection.add_argument(
            '--all',
            action='store_true',
            help=(
                "Rebuild every document, stale or not — for a tokenization "
                "change shipped without bumping TOKENIZER_VERSION."
            ),
        )
        selection.add_argument(
            '--unindexed-only',
            action='store_true',
            help=(
                "Only process documents the indexing task never completed "
                "against (tokenizer_version IS NULL) — the recovery path for "
                "uploads that landed while no Celery worker was consuming the "
                "queue, which main/migrations/0016_document_status_honesty.py "
                "flags as failed/never_indexed. Skips the content scan the "
                "default staleness check does."
            ),
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help=(
                "List the documents that would be retokenized, why, and their "
                "before/after token counts from the current tokenizer, without "
                "writing anything. With --all, covers every document — the way "
                "to review a tokenizer change (e.g. the nested-segment fix in "
                "docs/feature-list.md) against corpus_db before running for real."
            ),
        )

    def handle(self, *args, **options):
        selected = self._select(options)

        if not selected:
            self.stdout.write(self.style.SUCCESS("Nothing to retokenize — every document's tokens are current."))
            return

        reasons = Counter(reason for reason in selected.values() if reason)
        breakdown = ', '.join(f"{count} {reason}" for reason, count in sorted(reasons.items()))
        self.stdout.write(f"{len(selected)} document(s) to retokenize" + (f": {breakdown}." if breakdown else "."))

        if options['dry_run']:
            self._dry_run(selected)
            return

        # Delegates to the same task the worker runs for a new upload
        # (architecture plan §5) rather than duplicating its tokenize ->
        # hash -> aggregate sequence here — this command used to do that
        # itself and NOT recompute the Tier-2 aggregates
        # afterward, silently leaving them stale against the rebuilt
        # tokens (exactly the kind of drift docs/data-processing.md's
        # content-hash section warns about). Called directly rather than
        # via .delay() — this is already a synchronous, blocking CLI
        # command; there's no reason to introduce async dispatch (and a
        # dependency on a separately-running worker process) just to
        # retokenize from the command line.
        total = len(selected)
        failed = 0
        for i, document_id in enumerate(selected, start=1):
            try:
                index_document(document_id)
            except Exception:
                # index_document has already recorded the reason on the
                # document; carry on with the rest rather than stopping at the
                # first bad one.
                pass

            document = Document.objects.values(
                'title', 'status', 'status_reason', 'status_detail', 'token_count',
            ).get(id=document_id)

            if document['status'] == Document.STATUS_FAILED:
                # index_document records why rather than raising for a content
                # problem, so report it here instead of printing "0 tokens" and
                # letting it read as success.
                failed += 1
                self.stdout.write(self.style.WARNING(
                    f"[{i}/{total}] {document['title']}: FAILED "
                    f"({document['status_reason']}) — {document['status_detail']}"
                ))
            else:
                self.stdout.write(
                    f"[{i}/{total}] {document['title']}: "
                    f"{document['token_count']} tokens ({document['status']})"
                )

        if failed:
            self.stdout.write(self.style.WARNING(
                f"Retokenized {total - failed} of {total} document(s); {failed} failed."
            ))
        else:
            self.stdout.write(self.style.SUCCESS(f"Retokenized {total} document(s)."))

    def _select(self, options):
        """{document_id: staleness reason} in id order; the reason is None under
        --all, where documents are taken whether stale or not."""
        documents = Document.objects.all()
        if options['unindexed_only']:
            documents = documents.filter(tokenizer_version__isnull=True)
        if options['all'] or options['unindexed_only']:
            # Only ids — never the content, which index_document reads one
            # document at a time.
            return dict.fromkeys(documents.order_by('id').values_list('id', flat=True))
        # Unlike the reindex_stale_documents task, documents at 'indexing' are
        # included: running this by hand is how uploads that no worker ever
        # picked up get recovered.
        return find_stale_documents(documents)

    def _dry_run(self, selected):
        changed = 0
        documents = (
            Document.objects.order_by('id')
            .only('id', 'title', 'content', 'token_count')
            .iterator(chunk_size=SCAN_CHUNK_SIZE)
        )
        for document in documents:
            if document.id not in selected:
                continue
            reason = selected[document.id]
            suffix = f" [{reason}]" if reason else ""
            # The same check index_document runs before tokenizing — without
            # it, a document that would fail again reports the token count of
            # its own markup, as if retokenizing would fix it.
            failure, _ = diagnose_content(document.content, document.title)
            if failure:
                self.stdout.write(f"{document.title}: would fail ({failure}){suffix}")
                continue
            new_count = len(extract_words(document.content))
            delta = new_count - document.token_count
            if delta:
                changed += 1
            self.stdout.write(f"{document.title}: {document.token_count} -> {new_count} ({delta:+d}){suffix}")
        self.stdout.write(self.style.WARNING(
            f"Dry run only — no Token rows changed. {len(selected)} document(s) would be "
            f"retokenized, {changed} of them with a different token count."
        ))
