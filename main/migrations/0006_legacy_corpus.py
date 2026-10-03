# Puts every document that existed before corpora were introduced into one
# corpus, so nothing becomes invisible to analysis (documents in no corpus are
# excluded from every feature).

from django.db import migrations

LEGACY_CORPUS_NAME = 'Ungrouped (legacy import)'


def create_legacy_corpus(apps, schema_editor):
    Corpus = apps.get_model('main', 'Corpus')
    Document = apps.get_model('main', 'Document')

    documents = list(Document.objects.all())

    if not documents:
        return

    corpus = Corpus.objects.create(
        name=LEGACY_CORPUS_NAME,
        description='Files uploaded before corpora existed. Reorganize as needed.'
    )
    corpus.documents.set(documents)


def delete_legacy_corpus(apps, schema_editor):
    Corpus = apps.get_model('main', 'Corpus')
    Corpus.objects.filter(name=LEGACY_CORPUS_NAME).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0005_corpus'),
    ]

    operations = [
        migrations.RunPython(create_legacy_corpus, delete_legacy_corpus),
    ]
