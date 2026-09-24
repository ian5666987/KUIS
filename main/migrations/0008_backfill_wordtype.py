"""
Backfills WordType from every distinct Token.word, then points each
Token.word_type_id at the matching WordType — the data migration between
0007 (added word_type as nullable, kept word) and 0009 (drops word, makes
word_type required). Architecture plan §2, Tier 1.

Uses the historical model via apps.get_model() rather than importing
main.models directly, per Django's own migration-writing guidance — the
current models.py may have moved on by the time this migration actually
runs against someone else's database, but the historical model always
matches the schema shape AS OF this exact migration.
"""

from django.db import migrations


def backfill_word_type(apps, schema_editor):
    WordType = apps.get_model('main', 'WordType')
    Token = apps.get_model('main', 'Token')

    distinct_words = Token.objects.order_by().values_list('word', flat=True).distinct()

    existing = set(WordType.objects.values_list('form', flat=True))
    to_create = [WordType(form=w) for w in distinct_words if w not in existing]
    if to_create:
        WordType.objects.bulk_create(to_create, batch_size=5000, ignore_conflicts=True)

    word_to_id = dict(WordType.objects.values_list('form', 'id'))

    # UPDATE ... via bulk_update would need every row loaded into Python;
    # for the corpus sizes this project actually has today that's fine
    # (documented in optimisation_plan.md as "not the scaling risk" up to
    # tens of millions of tokens), and a raw UPDATE...FROM would tie this
    # migration to Postgres-specific SQL when the model itself stays
    # cross-database (see DocumentNgram's docstring on the same tradeoff).
    tokens = list(Token.objects.only('id', 'word'))
    for token in tokens:
        token.word_type_id = word_to_id[token.word]
    Token.objects.bulk_update(tokens, ['word_type_id'], batch_size=5000)


def noop_reverse(apps, schema_editor):
    # Reversing would mean reconstructing `word` from word_type — not
    # needed since 0009 (which drops `word`) is what actually makes this
    # migration irreversible; this migration alone loses no information.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('main', '0007_tier1_tier2_schema'),
    ]

    operations = [
        migrations.RunPython(backfill_word_type, noop_reverse),
    ]
