"""Wire-format response models for GET /api/v1/error-frequency, /epic, and
/error-summary. Request parameters are declared directly on each router as
FastAPI Query(...) params, same reasoning as schemas/kwic.py and
schemas/ranked.py."""

from pydantic import BaseModel


class ErrorFrequencyRowOut(BaseModel):
    phrase: str
    frequency: int


class ErrorFrequencyResponse(BaseModel):
    results: list[ErrorFrequencyRowOut]
    result_count: int
    type_count: int
    page: int
    per_page: int


class ErrorOccurrenceOut(BaseModel):
    document_id: int
    document_title: str
    left: list[str]
    keyword: list[str]
    right: list[str]
    error_code: str
    category_path: str
    correction_text: str


class EpicSearchResponse(BaseModel):
    results: list[ErrorOccurrenceOut]
    result_count: int
    page: int
    per_page: int
    window: int


class ErrorCategoryCountOut(BaseModel):
    category: str
    count: int


class ErrorSummaryResponse(BaseModel):
    total: int
    by_category: list[ErrorCategoryCountOut]
