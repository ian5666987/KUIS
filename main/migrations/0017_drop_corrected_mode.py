"""Drops the "corrected" text stream to save space.

Every document used to be tokenized twice — as written, and with each
segment's Correction substituted in — with a `mode` column telling the two
apart on Token, DocumentWordFreq and DocumentNgram. Only the original stream
is kept now, so this deletes the corrected rows (roughly half of each of
those three tables) and then drops the `mode` columns and
Document.token_count_corrected.

The delete has to come first: the rebuilt uniq_doc_wordfreq constraint is
(document, word_type), which the corrected rows would violate.

Irreversible in practice: reversing restores the columns (mode defaults to
'original') but not the deleted rows. Run `manage.py retokenize` on a
reverted schema only together with reverted code.

Follow with VACUUM (FULL) on main_token / main_documentwordfreq /
main_documentngram to hand the freed pages back to the OS — a plain DELETE
only makes them reusable by Postgres.
"""

from django.db import migrations, models


def delete_corrected_rows(apps, schema_editor):
    for model in ('Token', 'DocumentWordFreq', 'DocumentNgram'):
        apps.get_model('main', model).objects.filter(mode='corrected').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0016_document_status_honesty'),
    ]

    operations = [
        migrations.RunPython(delete_corrected_rows, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name='documentwordfreq',
            name='uniq_doc_wordfreq',
        ),
        migrations.RemoveIndex(
            model_name='documentngram',
            name='main_docume_documen_bcb01c_idx',
        ),
        migrations.RemoveIndex(
            model_name='documentwordfreq',
            name='main_docume_documen_0d76c1_idx',
        ),
        migrations.RemoveIndex(
            model_name='token',
            name='main_token_documen_223e5b_idx',
        ),
        migrations.RemoveField(
            model_name='document',
            name='token_count_corrected',
        ),
        migrations.AddIndex(
            model_name='documentngram',
            index=models.Index(fields=['document', 'n'], name='main_docume_documen_fd4104_idx'),
        ),
        migrations.AddIndex(
            model_name='token',
            index=models.Index(fields=['document', 'position'], name='main_token_documen_82364d_idx'),
        ),
        migrations.AddConstraint(
            model_name='documentwordfreq',
            constraint=models.UniqueConstraint(fields=('document', 'word_type'), name='uniq_doc_wordfreq'),
        ),
        migrations.RemoveField(
            model_name='documentngram',
            name='mode',
        ),
        migrations.RemoveField(
            model_name='documentwordfreq',
            name='mode',
        ),
        migrations.RemoveField(
            model_name='token',
            name='mode',
        ),
    ]
