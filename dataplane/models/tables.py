"""
SQLAlchemy Core `Table` objects hand-mirroring Django's tables
column-for-column (architecture plan §2). Django's `main/migrations/` is the
ONLY schema authority — nothing here issues a migration, and nothing here is
an ORM model with its own identity map. Plain Core tables, read (and in
later phases, written by the worker) via explicit `select`/`insert`
statements.

Table/column names below were confirmed against the real Django models via
introspection (`Model._meta.db_table` / `Field.column`), not guessed:

    Document          -> main_document          (id, title, content, uploaded_at,
                                                   user_id, token_count,
                                                   token_count_corrected, created_at,
                                                   status, content_hash, tokenized_hash,
                                                   tokenizer_version)
    Corpus            -> main_corpus            (id, name, description, created_by_id, created_at)
    Token             -> main_token             (id, document_id, word_type_id, position, mode)
    WordType          -> main_wordtype          (id, form)
    DocumentWordFreq  -> main_documentwordfreq  (id, document_id, word_type_id, mode, count)
    DocumentNgram     -> main_documentngram     (id, document_id, n, word_type_ids, mode, count)
    Corpus.documents (M2M) -> main_corpus_documents (id, corpus_id, document_id)

CI guard (architecture plan §2's "schema drift" caution): if a Django
migration renames/retypes any of these, dataplane/tests/test_schema_drift.py
fails loudly instead of these queries silently breaking or misreading data.

Phase 5: `token.word` (varchar) is GONE, replaced by `token.word_type_id`
(architecture plan §2, Tier 1 — "Token.word_type FK replacing Token.word").
`word_type`, `document_word_freq`, `document_ngram` (Tier 2) added.
"""

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Integer,
    JSON,
    MetaData,
    SmallInteger,
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
    Column("status", String(10)),
    Column("content_hash", String(64), nullable=True),
    Column("tokenized_hash", String(64), nullable=True),
    Column("tokenizer_version", Integer, nullable=True),
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
    Column("word_type_id", BigInteger),
    Column("position", Integer),
    Column("mode", String(10)),
)

word_type = Table(
    "main_wordtype",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("form", String(100)),
)

document_word_freq = Table(
    "main_documentwordfreq",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("document_id", BigInteger),
    Column("word_type_id", BigInteger),
    Column("mode", String(10)),
    Column("count", Integer),
)

document_ngram = Table(
    "main_documentngram",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("document_id", BigInteger),
    Column("n", SmallInteger),
    Column("word_type_ids", JSON),
    Column("mode", String(10)),
    Column("count", Integer),
)
