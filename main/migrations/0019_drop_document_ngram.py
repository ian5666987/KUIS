"""Drops DocumentNgram, the per-document top-500 n-gram table.

N-grams and collocations now count windows straight from main_token with one
`lead()` query (dataplane/repositories/postgres/_window_ngram_query.py).
Measured against this table on the real corpus and at catalogue and T1 sizes
(docs/hosting.md §3), that query was:

  - faster at every size: 2.8–3.7× today, ~2× at T1, because the table's read
    path shipped every candidate n-gram to Python to sort;
  - exact: the worker kept only the top 500 n-grams per (document, n), so long
    documents lost their tail from the corpus totals;
  - and it frees the table's 62% of corpus storage. 96.9% of its rows had
    count = 1, because n-grams in short essays repeat across documents, not
    within one.

No data step: nothing else references the table, and nothing derived from it
needs rebuilding. Reversing this recreates an EMPTY table; refilling it would
need the removed worker/tasks/aggregates.py::compute_document_ngrams from git
history.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('main', '0018_document_status_tokenized'),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name='documentngram',
            name='main_docume_documen_fd4104_idx',
        ),
        migrations.DeleteModel(
            name='DocumentNgram',
        ),
    ]
