"""Shared wire-format response models for /api/v1/frequency, /collocations,
and /ngrams — the three "ranked" analysis features share one response shape
(frequency and collocations use it as-is, ngrams adds `n`). Request
parameters are declared directly on each router as FastAPI Query(...)
params, same reasoning as schemas/kwic.py."""

from pydantic import BaseModel


class RankedRowOut(BaseModel):
    item: str
    count: int


class RankedSearchResponse(BaseModel):
    results: list[RankedRowOut]
    result_count: int
    type_count: int
    token_total: int
    page: int
    per_page: int


class NgramSearchResponse(RankedSearchResponse):
    n: int  # the n actually used, after NgramService's validation/fallback
