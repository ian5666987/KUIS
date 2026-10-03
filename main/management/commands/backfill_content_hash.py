from collections import defaultdict

from django.core.management.base import BaseCommand

from main.document_ingest import compute_content_hash
from main.models import Document


class Command(BaseCommand):
    help = (
        "Recomputes Document.content_hash/tokenized_hash for every existing "
        "document using the canonicalized formula in main/document_ingest.py "
        "(BOM/CRLF/trailing-whitespace/XML-attribute-order normalized) — "
        "older rows were hashed by worker/tasks/indexing.py's previous "
        "raw-content formula, which won't match a freshly-canonicalized hash "
        "of semantically identical new content until this runs once. Unlike "
        "retokenize.py, this does NOT touch Token/DocumentWordFreq/"
        "DocumentNgram rows — nothing about tokenization changed, only the "
        "hash formula did. Never writes two documents to the same "
        "content_hash: any group of documents whose content canonicalizes "
        "identically is left untouched and reported instead of merged — see "
        "docs/content-hash-dedup.md on why that decision isn't automatic. "
        "Idempotent; re-run after resolving a reported collision by hand. "
        "Must be run — and report zero collisions — BEFORE applying "
        "main/migrations/0010_document_content_hash_unique.py."
    )

    def handle(self, *args, **options):
        by_new_hash = defaultdict(list)
        for document in Document.objects.all().iterator():
            by_new_hash[compute_content_hash(document.content)].append(document)

        updated = 0
        collisions = []

        for new_hash, documents in by_new_hash.items():
            if len(documents) > 1:
                collisions.append((new_hash, documents))
                continue

            document = documents[0]
            update_fields = []
            if document.content_hash != new_hash:
                document.content_hash = new_hash
                update_fields.append("content_hash")
            if document.tokenized_hash is not None and document.tokenized_hash != new_hash:
                document.tokenized_hash = new_hash
                update_fields.append("tokenized_hash")
            if update_fields:
                document.save(update_fields=update_fields)
                updated += 1

        self.stdout.write(self.style.SUCCESS(f"Recomputed content_hash for {updated} document(s)."))

        if collisions:
            self.stdout.write(self.style.WARNING(
                f"{len(collisions)} pre-existing duplicate-content group(s) left untouched:"
            ))
            for _, documents in collisions:
                ids = ", ".join(f"#{d.id} ({d.title!r})" for d in documents)
                self.stdout.write(f"  {ids}")
            self.stdout.write(self.style.WARNING(
                "Resolve by hand, then re-run this command until it reports zero "
                "collisions — required before applying "
                "0010_document_content_hash_unique.py."
            ))
