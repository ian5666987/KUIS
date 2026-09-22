# base.py (Phase 2) defines the ABCs every feature repository implements —
# FrequencyRepository, CollocationRepository, NgramRepository,
# KWICRepository, ErrorAnalyticsRepository — so a later ClickHouse/OpenSearch
# implementation is a clean drop-in behind the same interface. See
# postgres/ for the Postgres implementations and architecture plan §1/§4.
