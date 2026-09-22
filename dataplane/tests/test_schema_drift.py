"""
Guards against dataplane/models/tables.py silently drifting from Django's
real schema (architecture plan §2) — Django's migrations are the only
schema authority, but this file is a second, hand-written description of
part of that schema, and nothing but this test keeps them in sync. A
migration that renames/drops a column this file reads would otherwise fail
at query time in production, not at review time.

Doesn't need a live database — pure introspection of Django's model
metadata (Model._meta), the same technique used to discover the real
table/column names in the first place (see dataplane/models/tables.py's
module docstring).
"""

import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

import pytest  # noqa: E402

from dataplane.models.tables import corpus, corpus_documents, document, token  # noqa: E402


def _django_columns(model) -> set[str]:
    return {f.column for f in model._meta.get_fields() if getattr(f, "column", None)}


@pytest.mark.parametrize(
    "sa_table,django_model_path,django_table_name",
    [
        ("document", "main.models.Document", "main_document"),
        ("corpus", "main.models.Corpus", "main_corpus"),
        ("token", "main.models.Token", "main_token"),
    ],
)
def test_table_name_and_columns_match_django(sa_table, django_model_path, django_table_name):
    from main import models as main_models

    model_name = django_model_path.rsplit(".", 1)[-1]
    django_model = getattr(main_models, model_name)

    assert django_model._meta.db_table == django_table_name

    sa_table_obj = {"document": document, "corpus": corpus, "token": token}[sa_table]
    sa_columns = {c.name for c in sa_table_obj.columns}
    django_columns = _django_columns(django_model)

    missing_in_sqlalchemy = django_columns - sa_columns
    extra_in_sqlalchemy = sa_columns - django_columns

    assert not missing_in_sqlalchemy, (
        f"Django's {model_name} has columns dataplane/models/tables.py's "
        f"`{sa_table}` doesn't: {missing_in_sqlalchemy}"
    )
    assert not extra_in_sqlalchemy, (
        f"dataplane/models/tables.py's `{sa_table}` has columns Django's "
        f"{model_name} doesn't (renamed/dropped column?): {extra_in_sqlalchemy}"
    )


def test_m2m_through_table_matches_django():
    from main.models import Corpus

    through = Corpus.documents.through
    assert through._meta.db_table == "main_corpus_documents"

    sa_columns = {c.name for c in corpus_documents.columns}
    django_columns = _django_columns(through)

    assert sa_columns == django_columns, (
        f"dataplane/models/tables.py's `corpus_documents` ({sa_columns}) doesn't "
        f"match Django's M2M through table ({django_columns})"
    )
