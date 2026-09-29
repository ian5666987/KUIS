from django.contrib import admin

# Register your models here.
from .models import Corpus, Document, DocumentErrorFreq, ErrorAnnotation, ErrorTaxonomyNode

# This is apparently how we register a model
admin.site.register(Document)


@admin.register(Corpus)
class CorpusAdmin(admin.ModelAdmin):
    list_display = ('name', 'created_by', 'is_public', 'created_at')
    list_filter = ('is_public',)
    list_editable = ('is_public',)
    filter_horizontal = ('documents',)


@admin.register(ErrorTaxonomyNode)
class ErrorTaxonomyNodeAdmin(admin.ModelAdmin):
    list_display = ('code', 'path', 'top_category', 'is_leaf')
    list_filter = ('top_category', 'is_leaf')
    search_fields = ('code', 'path', 'gloss')


@admin.register(ErrorAnnotation)
class ErrorAnnotationAdmin(admin.ModelAdmin):
    # state != 'active' rows are excluded from analytics reads by default
    # but kept (not deleted) for audit/debugging (docs/error-analytics-plan.md)
    # — this is the surface for reviewing them.
    list_display = ('document', 'raw_features', 'taxonomy_node', 'state', 'start_position', 'end_position')
    list_filter = ('state', 'taxonomy_node__top_category')
    search_fields = ('raw_features', 'original_text', 'correction_text')


@admin.register(DocumentErrorFreq)
class DocumentErrorFreqAdmin(admin.ModelAdmin):
    list_display = ('document', 'taxonomy_node', 'count')
    list_filter = ('taxonomy_node__top_category',)