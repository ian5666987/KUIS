"""Wire-format response models for GET /api/v1/error-frequency, /epic, and
/error-summary. Request parameters are declared directly on each router as
FastAPI Query(...) params, same reasoning as schemas/kwic.py and
schemas/ranked.py — including their note on the one shared exception,
schemas/metadata.py."""

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


class ErrorNodeCountOut(BaseModel):
    """`count` is subtree-inclusive and `self_count` is this node alone, so
    a client renders the expandable summary tree without re-aggregating
    anything (see ErrorNodeCount in dataplane/repositories/base.py). Nodes
    with no errors in scope are simply absent — a consumer joins this
    against GET /api/v1/taxonomy by `code` for the hierarchy and glosses."""

    code: str
    path: str
    count: int
    self_count: int


class ErrorSummaryResponse(BaseModel):
    total: int
    # The depth-1 roll-up, kept as-is: by_node carries the same numbers at
    # its top level, but this is the endpoint's original published shape.
    by_category: list[ErrorCategoryCountOut]
    by_node: list[ErrorNodeCountOut]
