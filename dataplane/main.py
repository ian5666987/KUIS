"""
FastAPI data plane — KWIC, frequency, n-gram, collocation, error analytics
(architecture plan §1). Phase 2 adds the first real feature: fast-KWIC,
ported from main/views.py::kwic_search and extended to phrase search (see
dataplane/repositories/postgres/kwic_repository.py). Frequency/collocation/
ngram/error_analytics routers land in Phase 4/6.

Run locally with: uvicorn dataplane.main:app --reload --port 8001
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from dataplane.core.config import settings
from dataplane.routers import health, kwic, whoami

app = FastAPI(
    title="KUIS Data Plane",
    debug=settings.debug,
)

# KUIS-FE calls this API directly from the browser, a genuinely cross-origin
# request (architecture plan §6) — same reasoning as Django's CorsMiddleware
# (config/settings.py). allow_headers must include Authorization, since
# that's how the bearer token travels (see dataplane/dependencies.py).
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins_list,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(health.router)
app.include_router(whoami.router)
app.include_router(kwic.router)
