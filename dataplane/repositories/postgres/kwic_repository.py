"""
Postgres implementation of KWICRepository (architecture plan §1/§4).

Ports main/views.py::_word_index_matches + _hydrate_word_index onto raw SQL,
generalized from single-word to an N-word phrase (see base.py's docstring on
why), and fixes the two known defects along the way
(docs/optimisation_plan.md):

  - defect #3: _word_index_matches materialized EVERY match into a Python
    list before Paginator sliced it. Here, pagination is a real SQL
    LIMIT/OFFSET, and the total count is a separate COUNT(*) query.
  - defect #4: _hydrate_word_index loaded a document's ENTIRE token stream
    to slice a +-window. Here, context is fetched with a `position BETWEEN`
    predicate, batched across the whole page in one query rather than one
    per hit.

Phase 5 update: joins WordType for the word string on both the match query
and the context-window query, since Token.word_type FK replaced Token.word
(architecture plan §2, Tier 1) — same reasoning and same fix shape as
_ranked_query.py's Phase 5 update. Unlike Frequency/Collocation/Ngram, KWIC
doesn't swap onto a Tier-2 aggregate table — concordance search is
inherently position-level (it needs the actual neighboring words, not a
count), which a precomputed aggregate can't answer. It reads normalized
Token/WordType directly, same as before, just via the FK instead of a
denormalized varchar.
"""

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from dataplane.models.tables import document, token, word_type
from dataplane.repositories.base import KWICHit, KWICRepository, KwicSort, Mode

# Safety bound for sort=left/right/document, which need the full match set
# assembled in memory before it can be sorted (see search()'s docstring) —
# not a real product limit, just a guard against a pathological corpus
# selection hanging a request. sort=center (the default) is unaffected and
# stays fully SQL-paginated regardless of result size.
MAX_UNPAGINATED_MATCHES = 10_000


class PostgresKWICRepository(KWICRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    def _match_query(self, document_ids: list[int], words: list[str], mode: Mode):
        """Returns (statement, base_alias) for the START position of each
        contiguous occurrence of `words`, via an N-way self-join on Token —
        one alias per word offset, each joined on document_id/mode matching
        the base alias and position = base.position + offset, plus a
        WordType join per alias to filter on the actual word string.

        Callers that need to ORDER BY document_id/position must use
        `base_alias.c.*`, not the unaliased `token` table — the FROM clause
        only contains the aliases (t0, t1, ...), so referencing the bare
        `token` table in the same query is a "relation does not exist" error
        at the SQL level, caught only by actually running this against
        Postgres (see dataplane/tests/test_kwic_repository.py)."""
        base = token.alias("t0")
        base_wt = word_type.alias("wt0")
        from_clause = base.join(base_wt, base_wt.c.id == base.c.word_type_id)
        conditions = [
            base.c.document_id.in_(document_ids),
            base.c.mode == mode.value,
            base_wt.c.form == words[0],
        ]

        for i in range(1, len(words)):
            t = token.alias(f"t{i}")
            wt = word_type.alias(f"wt{i}")
            from_clause = from_clause.join(
                t,
                and_(
                    t.c.document_id == base.c.document_id,
                    t.c.mode == base.c.mode,
                    t.c.position == base.c.position + i,
                ),
            ).join(wt, wt.c.id == t.c.word_type_id)
            conditions.append(wt.c.form == words[i])

        stmt = select(base.c.document_id, base.c.position).select_from(from_clause).where(and_(*conditions))
        return stmt, base

    async def count_matches(self, document_ids: list[int], words: list[str], mode: Mode) -> int:
        if not document_ids or not words:
            return 0

        stmt, _base = self._match_query(document_ids, words, mode)
        subquery = stmt.subquery()
        result = await self._session.execute(select(func.count()).select_from(subquery))
        return result.scalar_one()

    async def _hydrate_hits(
        self, hit_positions: list, words: list[str], mode: Mode, window: int
    ) -> list[KWICHit]:
        """Turns (document_id, position) match rows into full KWICHit
        objects — the context-window + title hydration shared by every
        sort mode. Batches every hit's context window into ONE query
        instead of one per hit (fixes defect #3) or loading whole
        documents (fixes defect #4)."""
        n = len(words)

        range_clauses = [
            and_(
                token.c.document_id == doc_id,
                token.c.mode == mode.value,
                token.c.position.between(pos - window, pos + n - 1 + window),
            )
            for doc_id, pos in hit_positions
        ]
        context_rows = (
            await self._session.execute(
                select(token.c.document_id, token.c.position, word_type.c.form)
                .select_from(token.join(word_type, word_type.c.id == token.c.word_type_id))
                .where(or_(*range_clauses))
                .order_by(token.c.document_id, token.c.position)
            )
        ).all()

        words_by_doc: dict[int, dict[int, str]] = {}
        for doc_id, position, form in context_rows:
            words_by_doc.setdefault(doc_id, {})[position] = form

        doc_ids_on_page = {doc_id for doc_id, _ in hit_positions}
        title_rows = (
            await self._session.execute(select(document.c.id, document.c.title).where(document.c.id.in_(doc_ids_on_page)))
        ).all()
        titles = dict(title_rows)

        hits = []
        for doc_id, pos in hit_positions:
            doc_words = words_by_doc.get(doc_id, {})
            left = [doc_words[p] for p in range(pos - window, pos) if p in doc_words]
            right = [doc_words[p] for p in range(pos + n, pos + n + window) if p in doc_words]

            hits.append(
                KWICHit(
                    document_id=doc_id,
                    document_title=titles.get(doc_id, ""),
                    left=left,
                    keyword=list(words),
                    right=right,
                )
            )

        return hits

    async def search(
        self,
        document_ids: list[int],
        words: list[str],
        mode: Mode,
        window: int,
        limit: int,
        offset: int,
        sort: KwicSort = KwicSort.CENTER,
    ) -> list[KWICHit]:
        if not document_ids or not words:
            return []

        matches, base = self._match_query(document_ids, words, mode)

        if sort == KwicSort.CENTER:
            # Corpus/document order — the only sort that needs nothing but
            # the page itself, so it stays fully SQL-paginated (real
            # LIMIT/OFFSET, defect #3's fix untouched).
            page_query = matches.order_by(base.c.document_id, base.c.position).limit(limit).offset(offset)
            hit_positions = (await self._session.execute(page_query)).all()
            if not hit_positions:
                return []
            return await self._hydrate_hits(hit_positions, words, mode, window)

        # left/right/document: the sort key (the adjacent word, or the
        # document title) only exists once a hit's context/title is
        # hydrated — which happens after the match query, not before. So
        # unlike `center`, this can't paginate in SQL first: fetch every
        # match (capped at MAX_UNPAGINATED_MATCHES), hydrate all of them,
        # sort, then slice — the same in-memory sort-before-paginate shape
        # main/views.py::_sort_kwic always used, just over indexed data.
        all_query = matches.order_by(base.c.document_id, base.c.position).limit(MAX_UNPAGINATED_MATCHES)
        hit_positions = (await self._session.execute(all_query)).all()
        if not hit_positions:
            return []

        hits = await self._hydrate_hits(hit_positions, words, mode, window)

        if sort == KwicSort.LEFT:
            # The single word immediately left of the match — main/views.py
            # ::_sort_kwic's `x["left"][-1] if x["left"] else ""`.
            hits.sort(key=lambda h: h.left[-1] if h.left else "")
        elif sort == KwicSort.RIGHT:
            hits.sort(key=lambda h: h.right[0] if h.right else "")
        elif sort == KwicSort.DOCUMENT:
            hits.sort(key=lambda h: h.document_title)

        return hits[offset : offset + limit]
