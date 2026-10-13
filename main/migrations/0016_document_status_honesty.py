"""Document.status stops lying about never-indexed documents.

Schema: the status default flips 'ready' -> 'indexing' (a Document row exists
before its tokens do), and status_reason/status_detail are added to carry WHY
a failed document failed.

Data: rows already stuck in the state the default change prevents are marked
failed. The signal is `tokenizer_version IS NULL` — that field is written only
by worker/tasks/indexing.py, so a NULL means the task never completed against
this row. It is paired with `token_count = 0` deliberately: documents indexed
before tokenizer_version existed (the pre-Phase-5 post_save signal, backfilled
to 'ready' by main/management/commands/backfill_tier2.py) also have a NULL
version but real token counts, and those are genuinely ready — this must not
sweep them up.

Recover the flagged documents with `manage.py retokenize --unindexed-only`,
which re-runs the real indexing task and moves them to ready (or to a failed
state with a specific parse reason, if the content is actually broken).
"""

from django.db import migrations, models


# Duplicated from main/corpus_parsing.py rather than imported: migrations are
# replayed against future versions of the codebase, where that constant may
# have moved or changed meaning. The value is what's frozen here.
REASON_NEVER_INDEXED = 'never_indexed'
DETAIL_NEVER_INDEXED = (
    'Indexing never ran for this document — most often the upload landed '
    'while the Celery worker was down. Re-run indexing to fix it.'
)


def flag_never_indexed_documents(apps, schema_editor):
    Document = apps.get_model('main', 'Document')
    Document.objects.filter(
        tokenizer_version__isnull=True,
        token_count=0,
        status='ready',
    ).update(
        status='failed',
        status_reason=REASON_NEVER_INDEXED,
        status_detail=DETAIL_NEVER_INDEXED,
    )


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0015_author_metadata'),
    ]

    operations = [
        migrations.AddField(
            model_name='document',
            name='status_detail',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='document',
            name='status_reason',
            field=models.CharField(blank=True, default='', max_length=32),
        ),
        migrations.AlterField(
            model_name='document',
            name='status',
            field=models.CharField(choices=[('indexing', 'Indexing'), ('ready', 'Ready'), ('failed', 'Failed')], default='indexing', max_length=10),
        ),
        # Last: it writes to status_reason/status_detail, which the AddFields
        # above create.
        migrations.RunPython(flag_never_indexed_documents, migrations.RunPython.noop),
    ]
