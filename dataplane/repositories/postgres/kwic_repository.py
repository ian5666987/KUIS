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

Ships against the raw Token.word varchar column (Tier 0 shape) — does not
wait for Tier 1's WordType normalization (Phase 5). That's the point of the
repository boundary: this file is the only thing that changes when Tier 1
lands, everything above it (Service/Router/Next.js) stays the same.
"""

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from dataplane.models.tables import document, token
from dataplane.repositories.base import KWICHit, KWICRepository, Mode


class PostgresKWICRepository(KWICRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    def _match_query(self, document_ids: list[int], words: list[str], mode: Mode):
        """Returns (statement, base_alias) for the START position of each
        contiguous occurrence of `words`, via an N-way self-join on Token —
        one alias per word offset, each joined on document_id/mode matching
        the base alias and position = base.position + offset.

        Callers that need to ORDER BY document_id/position must use
        `base_alias.c.*`, not the unaliased `token` table — the FROM clause
        only contains the aliases (t0, t1, ...), so referencing the bare
        `token` table in the same query is a "relation does not exist" error
        at the SQL level, caught only by actually running this against
        Postgres (see dataplane/tests/test_kwic_repository.py)."""
        base = token.alias("t0")
        from_clause = base
        conditions = [
            base.c.document_id.in_(document_ids),
            base.c.mode == mode.value,
            base.c.word == words[0],
        ]

        for i in range(1, len(words)):
            t = token.alias(f"t{i}")
            from_clause = from_clause.join(
                t,
                and_(
                    t.c.document_id == base.c.document_id,
                    t.c.mode == base.c.mode,
                    t.c.position == base.c.position + i,
                ),
            )
            conditions.append(t.c.word == words[i])

        stmt = select(base.c.document_id, base.c.position).select_from(from_clause).where(and_(*conditions))
        return stmt, base

    async def count_matches(self, document_ids: list[int], words: list[str], mode: Mode) -> int:
        if not document_ids or not words:
            return 0

        stmt, _base = self._match_query(document_ids, words, mode)
        subquery = stmt.subquery()
        result = await self._session.execute(select(func.count()).select_from(subquery))
        return result.scalar_one()

    async def search(
        self,
        document_ids: list[int],
        words: list[str],
        mode: Mode,
        window: int,
        limit: int,
        offset: int,
    ) -> list[KWICHit]:
        if not document_ids or not words:
            return []

        matches, base = self._match_query(document_ids, words, mode)
        page_query = matches.order_by(base.c.document_id, base.c.position).limit(limit).offset(offset)
        hit_positions = (await self._session.execute(page_query)).all()

        if not hit_positions:
            return []

        n = len(words)

        # Batch every hit's context window into ONE query instead of one per
        # hit (fixes defect #3) or loading whole documents (fixes defect #4).
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
                select(token.c.document_id, token.c.position, token.c.word)
                .where(or_(*range_clauses))
                .order_by(token.c.document_id, token.c.position)
            )
        ).all()

        words_by_doc: dict[int, dict[int, str]] = {}
        for doc_id, position, word in context_rows:
            words_by_doc.setdefault(doc_id, {})[position] = word

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
