from django.core.management.base import BaseCommand

from main.models import Document, Token, build_tokens


class Command(BaseCommand):
    help = (
        "Regenerates Token rows for every Document from its current content, "
        "using the XML-aware tokenizer (main.corpus_parsing). Use this after "
        "changing tokenization logic to fix documents that were tokenized "
        "before the change."
    )

    def handle(self, *args, **options):
        documents = Document.objects.all()
        total = documents.count()

        for i, document in enumerate(documents, start=1):
            Token.objects.filter(document=document).delete()
            token_count, token_count_corrected = build_tokens(document)
            document.token_count = token_count
            document.token_count_corrected = token_count_corrected
            document.save(update_fields=['token_count', 'token_count_corrected'])

            self.stdout.write(
                f"[{i}/{total}] {document.title}: "
                f"{token_count} original tokens, {token_count_corrected} corrected tokens"
            )

        self.stdout.write(self.style.SUCCESS(f"Retokenized {total} document(s)."))
