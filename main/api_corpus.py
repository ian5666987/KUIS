"""
Read-only corpus metadata for KUIS-FE's corpus picker (architecture plan §6,
Phase 3) — Django owns "Corpus Meta" in the target architecture, so this is
the first JSON endpoint exposing it, alongside the existing server-rendered
`corpus_dashboard` view.

The annotated queryset below intentionally duplicates `main/views.py`'s
private `_annotated_corpora()` rather than importing it: that function is
named with a leading underscore specifically as views.py-internal, and this
module is a different, additive surface (JSON API vs. server-rendered HTML)
that happens to need the same shape today. Five lines of duplication here is
a smaller risk than making a DRF view reach into another view module's
private helpers — if the two ever need to diverge (e.g. the API adds
pagination the HTML page doesn't), nothing has to be untangled first.
"""

from django.db.models import Count, Sum
from rest_framework import serializers
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated

from .models import Corpus


class CorpusSerializer(serializers.ModelSerializer):
    document_count = serializers.IntegerField()
    # Sum() over an empty/all-null set is NULL, not 0 — a corpus with no
    # documents (or whose documents all have token_count=0) would otherwise
    # serialize as `null`, forcing every consumer to null-check a count.
    # Matches main/views.py's own `totals['tokens'] or 0` pattern.
    token_total = serializers.SerializerMethodField()
    token_total_corrected = serializers.SerializerMethodField()

    def get_token_total(self, obj) -> int:
        return obj.token_total or 0

    def get_token_total_corrected(self, obj) -> int:
        return obj.token_total_corrected or 0

    class Meta:
        model = Corpus
        fields = [
            "id",
            "name",
            "description",
            "document_count",
            "token_total",
            "token_total_corrected",
            "created_at",
        ]


class CorpusListView(ListAPIView):
    """GET /api/corpora/ — any authenticated user (matches
    main/views.py::corpus_dashboard's plain @login_required; curation
    remains staff-only and server-rendered only, not exposed here)."""

    serializer_class = CorpusSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Corpus.objects.annotate(
            document_count=Count("documents", distinct=True),
            token_total=Sum("documents__token_count"),
            token_total_corrected=Sum("documents__token_count_corrected"),
        ).order_by("name")
