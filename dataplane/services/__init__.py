# Service layer between routers and repositories — the layer that stays
# untouched when a repository's storage backend is swapped (Postgres ->
# ClickHouse/OpenSearch), per the architecture plan's central design goal
# (§1). Populated starting Phase 2.
