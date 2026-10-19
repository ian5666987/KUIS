"""Document.status gains 'tokenized', and 'ready' starts meaning what it says.

Schema: the 'tokenized' choice — Token rows complete, aggregates not yet
(worker/tasks/indexing.py now advances indexing -> tokenized -> ready in two
tasks).

Data: until now index_document set 'ready' BEFORE dispatching the aggregate
tasks fire-and-forget, so a 'ready' document missing some or all of its
derived rows was a reachable state. Those documents are moved back to
'tokenized', because the data plane now admits only 'ready' documents to every
feature but KWIC — leaving them 'ready' would keep them in Frequency/N-grams/
Collocations/Error analysis while contributing nothing (or only half) there.

A ready document counts as incomplete when any of these hold:
  - no DocumentWordFreq rows (always >= 1 for a document with tokens);
  - two or more tokens but no DocumentNgram rows (any 2 tokens make a bigram,
    and the frequency floor is 1);
  - its content has a <segment> but it has no ErrorAnnotation rows (extraction
    records every segment, featureless ones included — so zero rows means
    extraction never ran);
  - it has active, resolved ErrorAnnotation rows but no DocumentErrorFreq rows.

The <segment> test is a substring match, so a false positive is possible (the
text "<segment" outside <body>). That's harmless: aggregate_documents re-runs
extraction, finds nothing, and marks the document ready again.

Finish the flagged documents with `manage.py aggregate_documents`.
"""

from django.db import migrations, models
from django.db.models import Exists, OuterRef, Q


def incomplete_ready_documents(apps):
    """The documents the data step demotes — split out so the set can be
    counted against a live database before migrating."""
    Document = apps.get_model('main', 'Document')
    DocumentWordFreq = apps.get_model('main', 'DocumentWordFreq')
    DocumentNgram = apps.get_model('main', 'DocumentNgram')
    ErrorAnnotation = apps.get_model('main', 'ErrorAnnotation')
    DocumentErrorFreq = apps.get_model('main', 'DocumentErrorFreq')

    same_document = {'document_id': OuterRef('pk')}
    has_word_freq = Exists(DocumentWordFreq.objects.filter(**same_document))
    has_ngrams = Exists(DocumentNgram.objects.filter(**same_document))
    has_annotations = Exists(ErrorAnnotation.objects.filter(**same_document))
    has_countable_annotations = Exists(
        ErrorAnnotation.objects.filter(state='active', taxonomy_node__isnull=False, **same_document)
    )
    has_error_freq = Exists(DocumentErrorFreq.objects.filter(**same_document))

    return Document.objects.filter(status='ready').filter(
        ~has_word_freq
        | (Q(token_count__gte=2) & ~has_ngrams)
        | (Q(content__contains='<segment') & ~has_annotations)
        | (has_countable_annotations & ~has_error_freq)
    )


def demote_incomplete_ready_documents(apps, schema_editor):
    incomplete_ready_documents(apps).update(status='tokenized', status_reason='', status_detail='')


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0017_drop_corrected_mode'),
    ]

    operations = [
        migrations.AlterField(
            model_name='document',
            name='status',
            field=models.CharField(
                choices=[('indexing', 'Indexing'), ('tokenized', 'Tokenized'), ('ready', 'Ready'), ('failed', 'Failed')],
                default='indexing',
                max_length=10,
            ),
        ),
        migrations.RunPython(demote_incomplete_ready_documents, migrations.RunPython.noop),
    ]
