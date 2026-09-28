from django.contrib import admin

# Register your models here.
from .models import Corpus, Document

# This is apparently how we register a model
admin.site.register(Document)


@admin.register(Corpus)
class CorpusAdmin(admin.ModelAdmin):
    list_display = ('name', 'created_by', 'is_public', 'created_at')
    list_filter = ('is_public',)
    list_editable = ('is_public',)
    filter_horizontal = ('documents',)