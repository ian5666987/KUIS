"""
FastAPI data plane — KWIC, frequency, n-gram, collocation, error analytics
(architecture plan §1). Phase 2 adds the first real feature: fast-KWIC,
ported from main/views.py::kwic_search and extended to phrase search (see
dataplane/repositories/postgres/kwic_repository.py). Frequency/collocation/
ngram/error_analytics routers land in Phase 4/6.

Run locally with: uvicorn dataplane.main:app --reload --port 8001
"""

from fastapi import FastAPI

from dataplane.core.config import settings
from dataplane.routers import health, kwic, whoami

app = FastAPI(
    title="KUIS Data Plane",
    debug=settings.debug,
)

app.include_router(health.router)
app.include_router(whoami.router)
app.include_router(kwic.router)
