"""
FastAPI data plane — KWIC, frequency, n-gram, collocation, error analytics
(architecture plan §1). PHASE 0: only /health is wired up. Feature routers
(frequency, collocation, ngram, kwic, error_analytics) and the repository/
service layers they depend on land starting Phase 2.

Run locally with: uvicorn dataplane.main:app --reload --port 8001
"""

from fastapi import FastAPI

from dataplane.core.config import settings
from dataplane.routers import health

app = FastAPI(
    title="KUIS Data Plane",
    debug=settings.debug,
)

app.include_router(health.router)
