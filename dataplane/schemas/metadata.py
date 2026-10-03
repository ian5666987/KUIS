"""
Secondary metadata filter: the one shared REQUEST schema in this package, plus
the facets response (docs/metadata-catalogue-plan.md).

Every other module here is response-only, and says so — "request parameters
are declared directly on the router as FastAPI Query(...) params rather than a
request schema". That reasoning still holds for per-feature params, each of
which needs its own validation. It does not hold for this filter: it is
identical on all seven analysis endpoints, so declaring it inline would mean
28 duplicated Query() declarations that have to be kept in step by hand. One
`Depends()`-able params object instead, which FastAPI still documents as plain
query params.

Param names match main/views.py's own GET params (`university`, `year`,
`grade`, `name_code`), same convention routers/frequency.py notes for `q`,
`match`, `sort` and `dir` — one vocabulary across both surfaces.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Query
from pydantic import BaseModel

from dataplane.repositories.base import METADATA_UNSET, MetadataFilter


@dataclass
class MetadataFilterParams:
    """The filter as it arrives on the wire.

    Each facet is a repeated param (`?university=TUFS&university=OU`), OR-ed
    within the facet and AND-ed across facets — the same shape `error_code`
    already uses on the error-analytics endpoints.

    A facet's "(no value)" option arrives as the sentinel METADATA_UNSET among
    that facet's own values, rather than as a separate `*_unset=true` flag:
    one param per facet, and it matches how the control behaves (just another
    checkbox in the list). The cost is that `year` and `grade` must be declared
    as strings and coerced in to_filter(), since the sentinel isn't an int.
    """

    university: Annotated[list[str] | None, Query()] = None
    year: Annotated[list[str] | None, Query()] = None
    grade: Annotated[list[str] | None, Query()] = None
    name_code: Annotated[str | None, Query()] = None

    def to_filter(self) -> MetadataFilter:
        """Builds the repository-layer filter. Request-shaping lives in the
        service layer by convention (see error_analytics_service.py's
        docstring on building ErrorFilter), and this is called from there.

        Junk on a numeric facet is dropped rather than 422-ing: the filter is a
        navigation control, so a stale or hand-edited URL should widen the
        result set, never break the page.
        """
        unset = set()

        def split(name, raw):
            values = [v.strip() for v in (raw or []) if v and v.strip()]
            if METADATA_UNSET in values:
                unset.add(name)
                values = [v for v in values if v != METADATA_UNSET]
            return values

        universities = split("university", self.university)
        raw_years = split("year", self.year)
        raw_grades = split("grade", self.grade)

        def as_ints(values):
            out = []
            for value in values:
                try:
                    out.append(int(value))
                except ValueError:
                    continue
            return tuple(out)

        return MetadataFilter(
            universities=tuple(universities),
            years=as_ints(raw_years),
            grades=as_ints(raw_grades),
            unset=frozenset(unset),
            name_code=(self.name_code or "").strip(),
        )


class MetadataFacetsResponse(BaseModel):
    """Available values per facet. `unset_value` is served rather than
    hardcoded in the frontend so the sentinel has exactly one definition the
    client can see — it's already duplicated once across the Django/dataplane
    boundary, which is enough."""

    universities: list[str]
    years: list[int]
    grades: list[int]
    unset_value: str = METADATA_UNSET
