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


class KwicSort(str, Enum):
    """Mirrors main/views.py::KWIC_SORTS from the legacy `kwic` view —
    restored to the merged FastAPI KWIC endpoint (KUIS-FE's "KWIC (Legacy)"
    page) after having been dropped in the original Phase 2 merge. `CENTER`
    (corpus/document order) is the only one that stays fully SQL-paginated;
    the other three need the whole match set assembled before they can be
    sorted (see PostgresKWICRepository.search's docstring) — same
    in-memory-sort-before-paginate shape the legacy view always had, just
    against the indexed Token table instead of a live re-parse."""

    CENTER = "center"
    LEFT = "left"
    RIGHT = "right"
    DOCUMENT = "document"


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
    search, so no separate port of that view is needed. `sort` restores
    legacy `kwic`'s sort-by-position control (see KwicSort)."""

    @abstractmethod
    async def search(
        self,
        document_ids: list[int],
        words: list[str],
        mode: Mode,
        window: int,
        limit: int,
        offset: int,
        sort: KwicSort = KwicSort.CENTER,
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
# Error Frequency + EPIC (Error Phrase In Context) — see
# docs/error-analytics-plan.md for the full design.
#
# `codes` is a flat list of taxonomy codes AT ANY DEPTH (a leaf like
# "ktinf" or a category like "gramatikal"), OR-combined — matches every
# leaf annotation that is EITHER that exact leaf OR a descendant of that
# category. This is the tree-picker's natural semantics: checking a
# category selects its whole subtree (a standard tree checkbox), checking
# a leaf selects just that one code, and checking several nodes (leaf or
# category, any branch) unions all of them. None/empty means no filter —
# every resolved+active annotation matches.
#
# Implemented as a single mechanism (postgres/_error_query.py): a leaf's
# own `path` already ends with its own code as the last segment, so
# "path contains code as a segment" correctly matches BOTH a direct leaf
# hit and a category-descendant hit with the same containment check — no
# separate exact-match branch needed.
#
# (Earlier revision of this design AND-combined a second "category_paths"
# axis for drill-down narrowing — e.g. "gramatikal" AND "frasa-nomina" to
# mean "under both". Replaced: once the taxonomy is a visual checkbox
# tree, AND-across-branches doesn't match how anyone actually expects a
# tree to behave — checking two nodes reads as "either", not "both",
# and to narrow to frasa-nomina specifically you just check that node.)


@dataclass(frozen=True)
class ErrorFilter:
    codes: list[str] | None = None


@dataclass(frozen=True)
class ErrorFrequencyRow:
    phrase: str  # the erroneous span's own original_text, verbatim (not lowercased — case is signal for some codes, e.g. ejk)
    frequency: int


@dataclass(frozen=True)
class ErrorFrequencyPage:
    rows: list[ErrorFrequencyRow]
    result_count: int  # rows matching the filter, before pagination
    type_count: int  # distinct phrases with NO filter applied


@dataclass(frozen=True)
class ErrorOccurrence:
    document_id: int
    document_title: str
    left: list[str]
    keyword: list[str]
    right: list[str]
    error_code: str
    category_path: str
    correction_text: str


@dataclass(frozen=True)
class ErrorCategoryCount:
    category: str  # a top_category label (e.g. "gramatikal")
    count: int


@dataclass(frozen=True)
class ErrorNodeCount:
    """One taxonomy node's share of the summary, at any depth below the
    literal root — the per-node breakdown the frontend's expandable
    summary tree traverses. `count` is already subtree-inclusive, so a
    client never has to sum its descendants; `self_count` is the subset
    resolved directly to this node, which is nonzero only for an
    annotation whose features stopped short of a leaf."""

    code: str  # unique across the whole taxonomy (e.g. "gramatikal", "nomafx")
    path: str  # semicolon-joined, root-first ("eror;gramatikal;frasa-nomina;nomafx")
    count: int  # this node plus every descendant
    self_count: int  # resolved to this exact node


@dataclass(frozen=True)
class ErrorSummary:
    total: int
    by_category: list[ErrorCategoryCount]
    by_node: list[ErrorNodeCount]


class ErrorAnalyticsRepository(ABC):
    @abstractmethod
    async def frequency(
        self,
        document_ids: list[int],
        filter: ErrorFilter,
        query: str | None,
        match_mode: MatchMode,
        sort: str,
        direction: SortDirection,
        limit: int,
        offset: int,
    ) -> ErrorFrequencyPage: ...

    @abstractmethod
    async def occurrences(
        self, document_ids: list[int], filter: ErrorFilter, window: int, limit: int, offset: int
    ) -> list[ErrorOccurrence]: ...

    @abstractmethod
    async def count_occurrences(self, document_ids: list[int], filter: ErrorFilter) -> int: ...

    @abstractmethod
    async def summary(self, document_ids: list[int]) -> ErrorSummary: ...


# --- Taxonomy --------------------------------------------------------------
# Reference data (info/error.xml, loaded into ErrorTaxonomyNode by
# `load_error_taxonomy` — Django-owned, FastAPI reads read-only), not a
# computation — kept as its own interface since other future features may
# also want the tree, same reasoning CollocationRepository is kept separate
# from NgramRepository.


@dataclass(frozen=True)
class TaxonomyNodeOut:
    code: str
    path: str
    gloss: str
    is_leaf: bool
    children: list["TaxonomyNodeOut"]


class TaxonomyRepository(ABC):
    @abstractmethod
    async def tree(self) -> list[TaxonomyNodeOut]: ...


# --- Document metadata -----------------------------------------------------
# The secondary filter axis (docs/metadata-catalogue-plan.md): catalogue
# fields imported from metadata/*.csv and attached to a Document. Unlike
# ErrorFilter this is NOT per-feature — it narrows the document set every
# feature runs over, so it is applied once in
# dataplane/repositories/shared.py::resolve_document_ids rather than reaching
# any repository method's signature.

# The sentinel meaning "documents with no value for this facet" — which covers
# BOTH a catalogue row whose field is empty and a document with no row at all.
# DUPLICATED from main/metadata_catalogue.py::UNSET: the dataplane can't
# import Django code, and api_corpus.py sets the precedent for duplicating a
# small shared value over reaching across surfaces. Keep the two in step.
METADATA_UNSET = "__none__"


@dataclass(frozen=True)
class MetadataFilter:
    """A parsed secondary metadata filter.

    Values and "unset" are separate because they compose with OR *within* a
    facet — "university is TUFS or has no value" is one checkbox list. Facets
    are then AND-ed with each other.

    `unset` is a frozenset of facet names rather than a sentinel left inside
    each value list, so the SQL builder never has to re-inspect strings.
    """

    universities: tuple[str, ...] = ()
    years: tuple[int, ...] = ()
    grades: tuple[int, ...] = ()
    unset: frozenset[str] = frozenset()
    name_code: str = ""

    @property
    def is_empty(self) -> bool:
        return not (
            self.universities or self.years or self.grades or self.unset or self.name_code
        )


@dataclass(frozen=True)
class MetadataFacets:
    """Distinct values available per facet, for populating the filter UI.

    Drawn only from catalogue rows that have a linked document, so a ticked
    box can never produce a guaranteed-empty result. Global rather than scoped
    to a corpus selection, matching TaxonomyRepository.tree() — a scoped list
    would reshuffle the controls every time a corpus is ticked.
    """

    universities: list[str]
    years: list[int]
    grades: list[int]


class MetadataRepository(ABC):
    @abstractmethod
    async def facets(self) -> MetadataFacets: ...
