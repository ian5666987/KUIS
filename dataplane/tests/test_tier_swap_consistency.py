"""
Direct proof that each repository swap is invisible in output, not just in
the Service/Router/Next.js layers (architecture plan §1's "the first live
proof of the repository-swap pattern the eventual ClickHouse/OpenSearch
migration depends on"). Tier 0 (_ranked_query.py's raw self-join over Token)
is the reference; each production path must produce an IDENTICAL RankedPage
for the same inputs:

  - word frequency: _aggregate_query.py's read of DocumentWordFreq;
  - n-grams and collocations: _window_ngram_query.py's lead() over Token.

The n-gram comparison is exact by construction now. The DocumentNgram table
it replaced kept only the top 500 n-grams per document, so it agreed with
Tier 0 only on fixtures too short to be truncated.

This is a stronger check than either path's own tests alone: those prove
each is independently correct against hand-computed expected values; this
proves they AGREE, which is the property a repository consumer depends on
when the swap happens under them.
"""

import pytest

from dataplane.repositories.base import MatchMode, SortDirection
from dataplane.repositories.postgres._aggregate_query import compute_word_freq_page
from dataplane.repositories.postgres._ranked_query import compute_ranked_page
from dataplane.repositories.postgres._window_ngram_query import compute_ngram_window_page


async def test_word_frequency_tier0_and_tier2_agree(session, document_ids):
    tier0 = await compute_ranked_page(
        session, document_ids, 1, None, MatchMode.CONTAINS, "count", SortDirection.DESC, 250, 0
    )
    tier2 = await compute_word_freq_page(
        session, document_ids, None, MatchMode.CONTAINS, "count", SortDirection.DESC, 250, 0
    )

    assert tier0.rows == tier2.rows
    assert tier0.result_count == tier2.result_count
    assert tier0.type_count == tier2.type_count
    assert tier0.token_total == tier2.token_total


async def test_word_frequency_tier0_and_tier2_agree_with_filter(session, document_ids):
    tier0 = await compute_ranked_page(
        session, document_ids, 1, "s", MatchMode.STARTS, "item", SortDirection.ASC, 250, 0
    )
    tier2 = await compute_word_freq_page(
        session, document_ids, "s", MatchMode.STARTS, "item", SortDirection.ASC, 250, 0
    )

    assert tier0.rows == tier2.rows
    assert tier0.result_count == tier2.result_count


# Every combination the ranked UI can send: each n the n-gram page offers
# (2 is also collocations), both sorts in both directions, every match mode,
# and pages that start mid-list.
NGRAM_CASES = [
    (n, query, match, sort, direction, limit, offset)
    for n in (2, 3, 4, 5)
    for query, match, sort, direction, limit, offset in [
        (None, MatchMode.CONTAINS, "count", SortDirection.DESC, 250, 0),
        (None, MatchMode.CONTAINS, "count", SortDirection.ASC, 250, 0),
        (None, MatchMode.CONTAINS, "item", SortDirection.ASC, 250, 0),
        (None, MatchMode.CONTAINS, "item", SortDirection.DESC, 250, 0),
        (None, MatchMode.CONTAINS, "count", SortDirection.DESC, 3, 2),
        ("nasi", MatchMode.CONTAINS, "count", SortDirection.DESC, 250, 0),
        ("saya", MatchMode.STARTS, "count", SortDirection.DESC, 250, 0),
        ("goreng", MatchMode.ENDS, "item", SortDirection.ASC, 250, 0),
        ("nasi goreng", MatchMode.EXACT, "count", SortDirection.DESC, 250, 0),
    ]
]


@pytest.mark.parametrize("n,query,match,sort,direction,limit,offset", NGRAM_CASES)
async def test_ngrams_tier0_and_window_query_agree(session, document_ids, n, query, match, sort, direction, limit, offset):
    tier0 = await compute_ranked_page(session, document_ids, n, query, match, sort, direction, limit, offset)
    window = await compute_ngram_window_page(session, document_ids, n, query, match, sort, direction, limit, offset)

    assert window.rows == tier0.rows
    assert window.result_count == tier0.result_count
    assert window.type_count == tier0.type_count
    assert window.token_total == tier0.token_total


@pytest.mark.parametrize(
    "query,offset",
    [
        ("zzz-matches-nothing", 0),  # filter matches nothing
        (None, 10_000),  # offset past the last row
    ],
)
async def test_window_query_empty_page_still_reports_both_counts(session, document_ids, query, offset):
    """The window query folds type_count, result_count and the page into ONE
    statement by LEFT JOINing the page onto a one-row base. An empty page
    must still carry both counts rather than collapsing to zero rows and
    losing them — the case a naive "count(*) OVER () on the page" would get
    wrong."""
    tier0 = await compute_ranked_page(
        session, document_ids, 2, query, MatchMode.CONTAINS, "count", SortDirection.DESC, 50, offset
    )
    window = await compute_ngram_window_page(
        session, document_ids, 2, query, MatchMode.CONTAINS, "count", SortDirection.DESC, 50, offset
    )

    assert window.rows == [] == tier0.rows
    assert window.type_count == tier0.type_count > 0
    assert window.result_count == tier0.result_count


async def test_window_query_with_no_documents_returns_an_empty_page(session):
    page = await compute_ngram_window_page(session, [], 2, None, MatchMode.CONTAINS, "count", SortDirection.DESC, 50, 0)

    assert page.rows == []
    assert (page.result_count, page.type_count, page.token_total) == (0, 0, 0)
