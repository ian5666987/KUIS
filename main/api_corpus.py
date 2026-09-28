"""
Corpus metadata + curation over the API (architecture plan §6, Phases 3 and
6). Django owns "Corpus Meta" in the target architecture, so all of this
stays here rather than moving to the FastAPI data plane.

Phase 3 added GET /api/corpora/ (read-only, any authenticated user). Phase 6
adds the write endpoints main/views.py's corpus_create/corpus_edit/
corpus_delete/corpus_assign have had since the original Django app, staff-
gated exactly like those views (@login_required @staff_required maps to
[IsAuthenticated, IsAdminUser] below — IsAdminUser alone checks is_staff,
but stacking IsAuthenticated first preserves the existing 401-for-anonymous
behavior main/tests.py::CorpusApiTests already pins, instead of falling
through to a bare 403).

The annotated queryset below intentionally duplicates main/views.py's private
_annotated_corpora() rather than importing it — same reasoning as the
original Phase 3 docstring: this module is a different, additive surface
(JSON API vs. server-rendered HTML) that happens to need the same shape
today. File-upload decoding and dedup-or-create instead go through
main/document_ingest.py (docs/content-hash-dedup.md), a neutral module both
this file and main/views.py import, rather than each having their own copy.
"""

from django.db.models import Count, Sum
from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .document_ingest import decode_uploaded_file, get_or_create_document
from .models import Corpus, Document
from worker.tasks.indexing import index_document

ADMIN_PERMISSIONS = [IsAuthenticated, IsAdminUser]


def _annotated_corpus_queryset():
    return Corpus.objects.annotate(
        document_count=Count("documents", distinct=True),
        token_total=Sum("documents__token_count"),
        token_total_corrected=Sum("documents__token_count_corrected"),
    )


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
            "is_public",
            "document_count",
            "token_total",
            "token_total_corrected",
            "created_at",
        ]


class CorpusDetailSerializer(CorpusSerializer):
    """Adds the per-document breakdown main/views.py::corpus_detail renders
    — id/title/token counts/status per document, so KUIS-FE's detail page
    doesn't need a second round trip to /api/documents/ just to render the
    corpus's own file list."""

    documents = serializers.SerializerMethodField()

    class Meta(CorpusSerializer.Meta):
        fields = CorpusSerializer.Meta.fields + ["documents"]

    def get_documents(self, obj):
        return [
            {
                "id": doc.id,
                "title": doc.title,
                "uploaded_at": doc.uploaded_at,
                "token_count": doc.token_count,
                "token_count_corrected": doc.token_count_corrected,
                "status": doc.status,
            }
            for doc in obj.documents.order_by("title")
        ]


class CorpusWriteSerializer(serializers.ModelSerializer):
    """Backs both create and update — mirrors main/forms.py::CorpusForm
    exactly, including its validation rule. document_ids is deliberately
    always a full desired-membership list, not a partial add/remove delta:
    main/views.py::corpus_create/corpus_edit always call
    corpus.documents.set(...), replacing the whole set on every save,
    because the Django form always re-submits every checkbox's state. The
    frontend's edit form does the same (submits the complete list every
    time), so this stays an exact behavioral match rather than inventing
    new partial-PATCH semantics the old app never had."""

    document_ids = serializers.PrimaryKeyRelatedField(
        source="documents", queryset=Document.objects.all(), many=True, required=False
    )

    class Meta:
        model = Corpus
        # is_public is an admin-only sharing choice (Corpus.is_public docstring,
        # main/models.py) — required=False comes for free from the model's
        # default=True, so a PATCH that omits it (there isn't one today; the
        # frontend always resubmits the full form) leaves it unchanged.
        fields = ["name", "description", "is_public", "document_ids"]

    def validate(self, attrs):
        documents = attrs.get("documents", [])
        files = self.context["request"].FILES.getlist("files")

        if not documents and not files:
            raise serializers.ValidationError(
                {"non_field_errors": ["Select at least one existing file or upload a new one."]}
            )

        return attrs


class CorpusListView(ListAPIView):
    """GET /api/corpora/ — any authenticated user (matches
    main/views.py::corpus_dashboard's plain @login_required), but private
    corpora (Corpus.is_public=False) are filtered out for non-staff —
    same rule corpus_dashboard applies to its own listing. POST — admin
    only, mirrors main/views.py::corpus_create exactly: same validation,
    same async indexing dispatch via index_document.delay(), same
    full-membership-replace semantics."""

    serializer_class = CorpusSerializer

    def get_permissions(self):
        if self.request.method == "POST":
            return [permission() for permission in ADMIN_PERMISSIONS]
        return [IsAuthenticated()]

    def get_queryset(self):
        queryset = _annotated_corpus_queryset().order_by("name")
        if not self.request.user.is_staff:
            queryset = queryset.filter(is_public=True)
        return queryset

    def post(self, request, *args, **kwargs):
        serializer = CorpusWriteSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)

        try:
            file_contents = [
                (f.name, decode_uploaded_file(f)) for f in request.FILES.getlist("files")
            ]
        except UnicodeDecodeError:
            return Response({"files": ["Uploaded files must be UTF-8 encoded."]}, status=400)

        documents = serializer.validated_data.pop("documents", [])
        corpus = Corpus.objects.create(created_by=request.user, **serializer.validated_data)

        resolved_documents = []
        deduplicated_count = 0
        for name, content in file_contents:
            doc, created = get_or_create_document(title=name, content=content, user=request.user)
            if created:
                index_document.delay(doc.id)  # async — see architecture plan §5
            else:
                deduplicated_count += 1
            resolved_documents.append(doc)

        corpus.documents.set(list(documents) + resolved_documents)

        output = CorpusDetailSerializer(_annotated_corpus_queryset().get(id=corpus.id)).data
        output["deduplicated_count"] = deduplicated_count
        return Response(output, status=status.HTTP_201_CREATED)


class CorpusDetailView(APIView):
    """GET /api/corpora/<id>/ — any authenticated user, mirrors
    main/views.py::corpus_detail, including its 404-not-403 handling of a
    private corpus a non-staff user has no business knowing exists. PATCH
    — admin only, mirrors main/views.py::corpus_edit. DELETE — admin only,
    mirrors main/views.py::corpus_delete (only the Corpus row + M2M join
    rows; Document rows always survive)."""

    def get_permissions(self):
        if self.request.method == "GET":
            return [IsAuthenticated()]
        return [permission() for permission in ADMIN_PERMISSIONS]

    def get(self, request, pk):
        corpus = get_object_or_404(_annotated_corpus_queryset(), id=pk)
        if not corpus.is_public and not request.user.is_staff:
            raise Http404
        return Response(CorpusDetailSerializer(corpus).data)

    def patch(self, request, pk):
        corpus = get_object_or_404(Corpus, id=pk)
        serializer = CorpusWriteSerializer(
            corpus, data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)

        try:
            file_contents = [
                (f.name, decode_uploaded_file(f)) for f in request.FILES.getlist("files")
            ]
        except UnicodeDecodeError:
            return Response({"files": ["Uploaded files must be UTF-8 encoded."]}, status=400)

        documents = serializer.validated_data.pop("documents", [])
        for field, value in serializer.validated_data.items():
            setattr(corpus, field, value)
        corpus.save()

        resolved_documents = []
        deduplicated_count = 0
        for name, content in file_contents:
            doc, created = get_or_create_document(title=name, content=content, user=request.user)
            if created:
                index_document.delay(doc.id)  # async — see architecture plan §5
            else:
                deduplicated_count += 1
            resolved_documents.append(doc)

        corpus.documents.set(list(documents) + resolved_documents)

        output = CorpusDetailSerializer(_annotated_corpus_queryset().get(id=corpus.id)).data
        output["deduplicated_count"] = deduplicated_count
        return Response(output)

    def delete(self, request, pk):
        corpus = get_object_or_404(Corpus, id=pk)
        corpus.delete()  # only the corpus and its M2M rows; documents survive
        return Response(status=status.HTTP_204_NO_CONTENT)


class CorpusAssignView(APIView):
    """POST /api/corpora/assign/ — admin only, mirrors
    main/views.py::corpus_assign exactly: bulk membership, additive only
    (a document keeps every corpus it already belonged to), no
    bulk-remove counterpart — matching the existing app, not adding a new
    capability."""

    permission_classes = ADMIN_PERMISSIONS

    def post(self, request):
        document_ids = request.data.get("document_ids", [])
        corpus_ids = request.data.get("corpus_ids", [])

        documents = list(Document.objects.filter(id__in=document_ids))
        corpora = list(Corpus.objects.filter(id__in=corpus_ids))

        if not documents or not corpora:
            return Response(
                {"non_field_errors": ["Pick at least one file and at least one corpus."]},
                status=400,
            )

        added = 0
        for corpus in corpora:
            already = set(corpus.documents.values_list("id", flat=True))
            new_documents = [doc for doc in documents if doc.id not in already]

            if new_documents:
                corpus.documents.add(*new_documents)
                added += len(new_documents)

        return Response({"added": added, "documents": len(documents), "corpora": len(corpora)})
