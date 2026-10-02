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

from .document_ingest import decode_uploaded_file, get_or_create_document
from .models import Document, DocumentMetadata
from worker.tasks.indexing import index_document

ADMIN_PERMISSIONS = [IsAuthenticated, IsAdminUser]


class DocumentMetadataSerializer(serializers.ModelSerializer):
    """Read-only view of a document's catalogue entry
    (docs/metadata-catalogue-plan.md). Writes go through
    `load_metadata_catalogue`, never the API — the CSV is the authority, so an
    editable field here would create a second, divergent one.

    Exposed so the document list can answer the operational question the
    importer's summary only answers in aggregate: which individual files have
    no metadata, and why a metadata filter excludes them.
    """

    class Meta:
        model = DocumentMetadata
        fields = [
            "source_filename",
            "university",
            "year",
            "grade",
            "topic",
            "topic_en",
            "word_count",
            "name_code",
        ]
        read_only_fields = fields


class DocumentSerializer(serializers.ModelSerializer):
    corpus_count = serializers.IntegerField()
    # None for a document the catalogue doesn't cover — which is a normal,
    # supported state, not an error (see the plan: uploads are accepted with or
    # without metadata). Reads via the reverse one-to-one, so the queryset
    # select_related's it to keep the list one query.
    metadata = DocumentMetadataSerializer(source="catalogue_entry", read_only=True)

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
            "metadata",
        ]


class DocumentListView(ListAPIView):
    """GET /api/documents/ — any authenticated user. This data was already
    in every logged-in user's page context under the old server-rendered
    corpus_dashboard (only the write actions built on it were staff-only),
    so listing stays IsAuthenticated, matching that precedent.
    ?unassigned=true mirrors corpus_dashboard's `unassigned` context
    variable, used by the assign-modal's document picker.
    ?has_metadata=false/true narrows to documents the metadata catalogue
    doesn't/does cover (docs/metadata-catalogue-plan.md)."""

    serializer_class = DocumentSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = (
            Document.objects.annotate(corpus_count=Count("corpora", distinct=True))
            # One query for the whole page's catalogue entries rather than one
            # per row — the serializer reads the reverse one-to-one.
            .select_related("catalogue_entry")
            .order_by("-uploaded_at")
        )

        if self.request.query_params.get("unassigned") == "true":
            queryset = queryset.filter(corpora__isnull=True)

        # Mirrors ?unassigned=true: lets an admin find exactly the files the
        # catalogue doesn't cover, which is otherwise only visible as a count
        # in the import command's summary.
        has_metadata = self.request.query_params.get("has_metadata")
        if has_metadata == "false":
            queryset = queryset.filter(catalogue_entry__isnull=True)
        elif has_metadata == "true":
            queryset = queryset.filter(catalogue_entry__isnull=False)

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
                content = decode_uploaded_file(uploaded_file)
            except UnicodeDecodeError:
                return Response({"file": ["File must be UTF-8 encoded."]}, status=400)

        doc, created = get_or_create_document(title=title, content=content, user=request.user)
        if created:
            index_document.delay(doc.id)  # async — see architecture plan §5

        output = DocumentSerializer(
            Document.objects.annotate(corpus_count=Count("corpora", distinct=True))
            # The response carries the catalogue entry get_or_create_document
            # just linked (or null when the catalogue doesn't cover this file),
            # so the uploader can see immediately whether it will be reachable
            # by a metadata filter.
            .select_related("catalogue_entry")
            .get(id=doc.id)
        ).data
        output["duplicate"] = not created
        return Response(output, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)
