"""
Abstract repository interfaces — the boundary the whole revamp is organized
around (architecture plan §1). A router never queries Postgres directly; it
goes through a Service, which goes through one of these interfaces. Postgres
implementations live in repositories/postgres/. A future ClickHouse
implementation (Frequency/Collocation/Ngram) or OpenSearch implementation
(KWIC) is a second concrete class implementing the SAME interface here —
nothing above the repository layer (Service, Router, Next.js) should need to
change when that happens.

PHASE 2: only KWICRepository has a concrete implementation
(postgres/kwic_repository.py). The other four are defined now, shaped after
their corresponding main/views.py logic, so later ports (Phase 4/6) slot
into an already-agreed contract instead of each inventing its own — but
treat their exact shape as provisional until a real implementation exercises
it; KWIC's interface is the one that's actually been proven against data.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


class Mode(str, Enum):
    """Mirrors main/models.py::Token.ORIGINAL / Token.CORRECTED."""

    ORIGINAL = "original"
    CORRECTED = "corrected"


class MatchMode(str, Enum):
    """Mirrors main/views.py::MATCH_MODES."""

    CONTAINS = "contains"
    STARTS = "starts"
    ENDS = "ends"
    EXACT = "exact"


class SortDirection(str, Enum):
    ASC = "asc"
    DESC = "desc"


# --- KWIC --------------------------------------------------------------
# The one interface Phase 2 actually implements and proves against data.


@dataclass(frozen=True)
class KWICHit:
    document_id: int
    document_title: str
    left: list[str]
    keyword: list[str]
    right: list[str]


class KWICRepository(ABC):
    """`words` is a phrase (list, not a single string) deliberately — see
    architecture plan §4: extending the fast (Token-table) path to accept a
    phrase makes it a strict superset of legacy `kwic`'s live-parsed phrase
    search, so no separate port of that view is needed."""

    @abstractmethod
    async def search(
        self,
        document_ids: list[int],
        words: list[str],
        mode: Mode,
        window: int,
        limit: int,
        offset: int,
    ) -> list[KWICHit]: ...

    @abstractmethod
    async def count_matches(self, document_ids: list[int], words: list[str], mode: Mode) -> int: ...


# --- Frequency / Collocation / Ngram ------------------------------------
# Provisional shape, not yet implemented (Phase 4). Modeled on
# main/views.py::_rank_counter's filter -> sort -> paginate pipeline, which
# is identical across all three features today (word_frequency,
# collocations, ngrams all call _count_words + _rank_counter the same way).


@dataclass(frozen=True)
class RankedRow:
    item: str  # a word, a "word1 word2" pair, or an n-gram tuple joined by spaces
    count: int


@dataclass(frozen=True)
class RankedPage:
    rows: list[RankedRow]
    result_count: int  # rows matching the filter, before pagination
    type_count: int  # distinct types with NO filter applied
    token_total: int  # total token occurrences the corpus selection covers


class FrequencyRepository(ABC):
    @abstractmethod
    async def ranked(
        self,
        document_ids: list[int],
        mode: Mode,
        query: str | None,
        match_mode: MatchMode,
        sort: str,
        direction: SortDirection,
        limit: int,
        offset: int,
    ) -> RankedPage: ...


class CollocationRepository(ABC):
    """Kept as its own interface rather than NgramRepository with n=2 fixed
    — collocations have their own product future (e.g. mutual-information
    scoring) a generic n-gram interface shouldn't be forced to carry. A
    Postgres implementation may still delegate internally to the same query
    builder as NgramRepository; that's an implementation detail, not part of
    this contract."""

    @abstractmethod
    async def ranked(
        self,
        document_ids: list[int],
        mode: Mode,
        query: str | None,
        match_mode: MatchMode,
        sort: str,
        direction: SortDirection,
        limit: int,
        offset: int,
    ) -> RankedPage: ...


class NgramRepository(ABC):
    @abstractmethod
    async def ranked(
        self,
        document_ids: list[int],
        mode: Mode,
        n: int,
        query: str | None,
        match_mode: MatchMode,
        sort: str,
        direction: SortDirection,
        limit: int,
        offset: int,
    ) -> RankedPage: ...


# --- Error Analytics ------------------------------------------------------
# The most provisional of the five — an entirely new feature (no Django view
# to port), blocked on a documented error-type taxonomy (docs/ONBOARDING.md
# §3/§9 in this repo). Shape below is a placeholder guess at "counts grouped
# by the segment `features` tag path" (e.g. "eror;leksikal;kata;ktinf" from
# main/corpus_parsing.py's XML schema) — expect this to change once the
# taxonomy exists and Phase 6 actually builds it.


@dataclass(frozen=True)
class ErrorCategoryCount:
    category_path: str
    count: int


class ErrorAnalyticsRepository(ABC):
    @abstractmethod
    async def category_counts(
        self, document_ids: list[int], limit: int, offset: int
    ) -> list[ErrorCategoryCount]: ...

    @abstractmethod
    async def count_categories(self, document_ids: list[int]) -> int: ...
