"""Shared across every feature service — main/views.py's PER_PAGE_CHOICES /
DEFAULT_PER_PAGE / _get_per_page are module-level constants used by ALL
analysis views (word frequency, collocations, n-grams, KWIC alike), not
duplicated per feature. Extracted here in Phase 4 once a second/third/fourth
service needed the exact same constants kwic_service.py already had."""

PER_PAGE_CHOICES = (25, 50, 100, 250)
DEFAULT_PER_PAGE = 50


def normalize_per_page(per_page: int) -> int:
    return per_page if per_page in PER_PAGE_CHOICES else DEFAULT_PER_PAGE
