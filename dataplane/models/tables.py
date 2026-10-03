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

docs/error-analytics-plan.md: `error_taxonomy_node`, `error_annotation`,
`document_error_freq` added — mirrors main.models.{ErrorTaxonomyNode,
ErrorAnnotation, DocumentErrorFreq} column-for-column, same as every other
table here.

docs/metadata-catalogue-plan.md: `document_metadata` added, mirroring
main.models.DocumentMetadata. Note `main_document` itself is deliberately
UNCHANGED by that feature — the FK points this way, so nothing above had to
be re-mirrored.
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
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
    # Pre-existing drift, fixed in passing while touching this file for
    # docs/error-analytics-plan.md: added by main/migrations/0011_corpus_
    # is_public.py but never mirrored here — exactly the trap this file's
    # own module docstring (and test_schema_drift.py) warns about.
    Column("is_public", Boolean),
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

error_taxonomy_node = Table(
    "main_errortaxonomynode",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("code", String(50)),
    Column("parent_id", BigInteger, nullable=True),
    Column("path", String(255)),
    Column("gloss", Text),
    Column("is_leaf", Boolean),
    Column("top_category", String(50), nullable=True),
)

error_annotation = Table(
    "main_errorannotation",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("document_id", BigInteger),
    Column("taxonomy_node_id", BigInteger, nullable=True),
    Column("raw_features", String(255)),
    Column("parent_id", BigInteger, nullable=True),
    Column("source_segment_id", String(50)),
    Column("start_position", Integer),
    Column("end_position", Integer),
    Column("original_text", Text),
    Column("correction_text", Text),
    Column("state", String(20)),
    Column("comment", Text),
)

document_error_freq = Table(
    "main_documenterrorfreq",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("document_id", BigInteger),
    Column("taxonomy_node_id", BigInteger),
    Column("count", Integer),
)

# docs/metadata-catalogue-plan.md. `document_id` is nullable because a
# catalogue row exists whether or not its file has been uploaded yet — and
# every analysis column is nullable because the secondary filter's "(no
# value)" option has to treat "entry exists, field empty" and "no entry at
# all" alike. Joined LEFT OUTER from the document side in
# dataplane/repositories/shared.py, which is what makes NULL mean both.
document_metadata = Table(
    "main_documentmetadata",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("source_filename", String(200)),
    Column("match_key", String(200)),
    Column("document_id", BigInteger, nullable=True),
    Column("university", String(50), nullable=True),
    Column("year", Integer, nullable=True),
    Column("grade", Integer, nullable=True),
    Column("topic", String(100), nullable=True),
    Column("topic_en", String(100), nullable=True),
    Column("word_count", Integer, nullable=True),
    Column("name_code", String(50), nullable=True),
    Column("source_file", String(100)),
    Column("imported_at", DateTime(timezone=True)),
)
