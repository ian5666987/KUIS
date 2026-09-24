"""
Direct proof that the Phase 5 repository swap is invisible in output, not
just in the Service/Router/Next.js layers (architecture plan §1's "the
first live proof of the repository-swap pattern the eventual ClickHouse/
OpenSearch migration depends on"): Tier 0 (_ranked_query.py's raw self-join
over Token) and Tier 2 (_aggregate_query.py's read of the worker-populated
DocumentWordFreq/DocumentNgram) must produce IDENTICAL RankedPages for the
same inputs, on this fixture where DocumentNgram's top-K-per-document cap
(worker/tasks/aggregates.py::TOP_K_PER_DOCUMENT=500) never actually
truncates anything.

This is a stronger check than either tier's own test suite alone: those
prove each tier is independently correct against hand-computed expected
values; this proves the two tiers AGREE with each other, which is the
actual property a repository consumer depends on when the swap happens
under them.
"""

from dataplane.repositories.base import MatchMode, Mode, SortDirection
from dataplane.repositories.postgres._aggregate_query import compute_ngram_agg_page, compute_word_freq_page
from dataplane.repositories.postgres._ranked_query import compute_ranked_page


async def test_word_frequency_tier0_and_tier2_agree(session, document_ids):
    tier0 = await compute_ranked_page(
        session, document_ids, Mode.ORIGINAL, 1, None, MatchMode.CONTAINS, "count", SortDirection.DESC, 250, 0
    )
    tier2 = await compute_word_freq_page(
        session, document_ids, Mode.ORIGINAL, None, MatchMode.CONTAINS, "count", SortDirection.DESC, 250, 0
    )

    assert tier0.rows == tier2.rows
    assert tier0.result_count == tier2.result_count
    assert tier0.type_count == tier2.type_count
    assert tier0.token_total == tier2.token_total


async def test_word_frequency_tier0_and_tier2_agree_with_filter_and_corrected_mode(session, document_ids):
    tier0 = await compute_ranked_page(
        session, document_ids, Mode.CORRECTED, 1, "s", MatchMode.STARTS, "item", SortDirection.ASC, 250, 0
    )
    tier2 = await compute_word_freq_page(
        session, document_ids, Mode.CORRECTED, "s", MatchMode.STARTS, "item", SortDirection.ASC, 250, 0
    )

    assert tier0.rows == tier2.rows
    assert tier0.result_count == tier2.result_count


async def test_collocations_tier0_and_tier2_agree(session, document_ids):
    tier0 = await compute_ranked_page(
        session, document_ids, Mode.ORIGINAL, 2, None, MatchMode.CONTAINS, "count", SortDirection.DESC, 250, 0
    )
    tier2 = await compute_ngram_agg_page(
        session, document_ids, Mode.ORIGINAL, 2, None, MatchMode.CONTAINS, "count", SortDirection.DESC, 250, 0
    )

    assert tier0.rows == tier2.rows
    assert tier0.result_count == tier2.result_count
    assert tier0.type_count == tier2.type_count
    assert tier0.token_total == tier2.token_total


async def test_trigrams_tier0_and_tier2_agree(session, document_ids):
    tier0 = await compute_ranked_page(
        session, document_ids, Mode.ORIGINAL, 3, None, MatchMode.CONTAINS, "count", SortDirection.DESC, 250, 0
    )
    tier2 = await compute_ngram_agg_page(
        session, document_ids, Mode.ORIGINAL, 3, None, MatchMode.CONTAINS, "count", SortDirection.DESC, 250, 0
    )

    assert tier0.rows == tier2.rows
    assert tier0.result_count == tier2.result_count
