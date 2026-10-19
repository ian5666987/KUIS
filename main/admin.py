from django import forms
from django.contrib import admin

# Register your models here.
from .content_hash import compute_content_hash
from .models import Corpus, Document, DocumentErrorFreq, ErrorAnnotation, ErrorTaxonomyNode


class DocumentAdminForm(forms.ModelForm):
    class Meta:
        model = Document
        fields = '__all__'

    def clean_content(self):
        # Document.save() recomputes content_hash from the edited text, and
        # content_hash is unique — so text identical to another document's
        # would otherwise surface as an IntegrityError page on save.
        content = self.cleaned_data['content']
        duplicate = (
            Document.objects.filter(content_hash=compute_content_hash(content))
            .exclude(pk=self.instance.pk)
            .only('id', 'title')
            .first()
        )
        if duplicate:
            raise forms.ValidationError(
                f'Document #{duplicate.id} ("{duplicate.title}") already has this content.'
            )
        return content


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    form = DocumentAdminForm
    # Derived, never typed: content_hash from `content` (Document.save()),
    # tokenized_hash/tokenizer_version by the indexing task
    # (worker/tasks/indexing.py). Hand-edited, they would mis-key upload dedup
    # or hide stale tokens from worker/tasks/maintenance.py.
    readonly_fields = ('content_hash', 'tokenized_hash', 'tokenizer_version')


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
    # but kept (not deleted) for audit/debugging (docs/feature-list.md)
    # — this is the surface for reviewing them.
    list_display = ('document', 'raw_features', 'taxonomy_node', 'state', 'start_position', 'end_position')
    list_filter = ('state', 'taxonomy_node__top_category')
    search_fields = ('raw_features', 'original_text', 'correction_text')


@admin.register(DocumentErrorFreq)
class DocumentErrorFreqAdmin(admin.ModelAdmin):
    list_display = ('document', 'taxonomy_node', 'count')
    list_filter = ('taxonomy_node__top_category',)