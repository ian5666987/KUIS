# SQLAlchemy Table definitions mirroring Django's tables column-for-column
# (architecture plan §2). Django's main/migrations/ stays the only schema
# authority — nothing here issues migrations. Populated starting Phase 2
# (tables.py for Token/Document, read-only) and extended in Phase 5 for the
# Tier 1/2 tables (WordType, DocumentWordFreq, DocumentNgram).
