"""Wire-format response models for GET /api/v1/kwic. Request parameters are
declared directly on the router as FastAPI Query(...) params rather than a
request schema — idiomatic for a GET endpoint, and each param needs its own
validation (window clamping, per_page allowlist) that belongs in the
service layer anyway (see kwic_service.py)."""

from pydantic import BaseModel


class KWICHitOut(BaseModel):
    document_id: int
    document_title: str
    left: list[str]
    keyword: list[str]
    right: list[str]


class KWICSearchResponse(BaseModel):
    results: list[KWICHitOut]
    result_count: int
    page: int
    per_page: int
    window: int
    corrected: bool
