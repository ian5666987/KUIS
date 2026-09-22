"""
SQLAlchemy Core `Table` objects hand-mirroring Django's tables
column-for-column (architecture plan §2). Django's `main/migrations/` is the
ONLY schema authority — nothing here issues a migration, and nothing here is
an ORM model with its own identity map. Plain Core tables, read (and in
later phases, written by the worker) via explicit `select`/`insert`
statements.

Table/column names below were confirmed against the real Django models via
introspection (`Model._meta.db_table` / `Field.column`), not guessed:

    Document  -> main_document      (id, title, content, uploaded_at, user_id,
                                      token_count, token_count_corrected, created_at)
    Corpus    -> main_corpus        (id, name, description, created_by_id, created_at)
    Token     -> main_token         (id, document_id, word, position, mode)
    Corpus.documents (M2M) -> main_corpus_documents (id, corpus_id, document_id)

CI guard (architecture plan §2's "schema drift" caution): if a Django
migration renames/retypes any of these, dataplane/tests/test_schema_drift.py
fails loudly instead of these queries silently breaking or misreading data.

PHASE 2 scope: only the four tables fast-KWIC needs. WordType,
DocumentWordFreq, DocumentNgram (Tier 1/2) are added in Phase 5.
"""

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)

metadata = MetaData()

document = Table(
    "main_document",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("title", String(200)),
    Column("content", Text),
    Column("uploaded_at", DateTime(timezone=True)),
    Column("user_id", BigInteger, nullable=True),
    Column("token_count", Integer),
    Column("token_count_corrected", Integer),
    Column("created_at", DateTime(timezone=True)),
)

corpus = Table(
    "main_corpus",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("name", String(200)),
    Column("description", Text),
    Column("created_by_id", BigInteger, nullable=True),
    Column("created_at", DateTime(timezone=True)),
)

corpus_documents = Table(
    "main_corpus_documents",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("corpus_id", BigInteger),
    Column("document_id", BigInteger),
)

token = Table(
    "main_token",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("document_id", BigInteger),
    Column("word", String(100)),
    Column("position", Integer),
    Column("mode", String(10)),
)
