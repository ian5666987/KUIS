"""
FastAPI data plane — KWIC, frequency, n-gram, collocation, error analytics
(architecture plan §1). Phase 2 ported fast-KWIC. Phase 4 adds word
frequency, collocations, and n-grams, all three sharing one query builder
(dataplane/repositories/postgres/_ranked_query.py) at different values of
n. error_analytics is the one router still missing — Phase 6, blocked on
the error-type taxonomy.

Run locally with: uvicorn dataplane.main:app --reload --port 8001
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from dataplane.core.config import settings
from dataplane.routers import collocations, frequency, health, kwic, ngrams, whoami

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
app.include_router(frequency.router)
app.include_router(collocations.router)
app.include_router(ngrams.router)
