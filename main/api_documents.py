"""
Document listing + upload over the API (architecture plan §6, Phase 6).
Kept as its own module rather than folded into main/api_corpus.py since
Document and Corpus are separate resources with separate serializers —
matching main/models.py's own layout (both models live in one file, but
main/forms.py already splits CorpusForm/DocumentForm the same way).
"""

from django.db.models import Count
from rest_framework import serializers, status
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Document
from worker.tasks.indexing import index_document

ADMIN_PERMISSIONS = [IsAuthenticated, IsAdminUser]


def _document_from_upload(uploaded_file, user, title=None):
    """Duplicated from main/views.py — see main/api_corpus.py's module
    docstring for why these API modules stay decoupled from views.py's own
    underscore-prefixed internals rather than importing them."""
    content = uploaded_file.read().decode("utf-8")
    return Document(title=title or uploaded_file.name, content=content, user=user)


class DocumentSerializer(serializers.ModelSerializer):
    corpus_count = serializers.IntegerField()

    class Meta:
        model = Document
        fields = [
            "id",
            "title",
            "uploaded_at",
            "token_count",
            "token_count_corrected",
            "status",
            "corpus_count",
        ]


class DocumentListView(ListAPIView):
    """GET /api/documents/ — any authenticated user. This data was already
    in every logged-in user's page context under the old server-rendered
    corpus_dashboard (only the write actions built on it were staff-only),
    so listing stays IsAuthenticated, matching that precedent.
    ?unassigned=true mirrors corpus_dashboard's `unassigned` context
    variable, used by the assign-modal's document picker."""

    serializer_class = DocumentSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = Document.objects.annotate(
            corpus_count=Count("corpora", distinct=True)
        ).order_by("-uploaded_at")

        if self.request.query_params.get("unassigned") == "true":
            queryset = queryset.filter(corpora__isnull=True)

        return queryset


class DocumentUploadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        fields = ["title", "content"]
        extra_kwargs = {"content": {"required": False, "allow_blank": True}}


class DocumentUploadView(APIView):
    """POST /api/documents/upload/ — admin only, mirrors
    main/views.py::upload_document exactly: title + optional pasted content
    + optional file (file content wins over pasted content if both are
    given), UTF-8-only, and deliberately does NOT attach the new document
    to any corpus — matching the old view's own user-facing copy ("Add it
    to a corpus to include it in analyses.")."""

    permission_classes = ADMIN_PERMISSIONS

    def post(self, request):
        serializer = DocumentUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        title = serializer.validated_data["title"]
        content = serializer.validated_data.get("content", "")
        uploaded_file = request.FILES.get("file")

        if not content and not uploaded_file:
            return Response(
                {"non_field_errors": ["You must provide either text or a file."]}, status=400
            )

        if uploaded_file:
            try:
                content = _document_from_upload(uploaded_file, request.user, title=title).content
            except UnicodeDecodeError:
                return Response({"file": ["File must be UTF-8 encoded."]}, status=400)

        doc = Document.objects.create(title=title, content=content, user=request.user)
        index_document.delay(doc.id)  # async — see architecture plan §5

        output = DocumentSerializer(
            Document.objects.annotate(corpus_count=Count("corpora", distinct=True)).get(id=doc.id)
        ).data
        return Response(output, status=status.HTTP_201_CREATED)
