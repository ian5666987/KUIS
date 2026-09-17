from django.contrib import admin

# Register your models here.
from .models import Corpus, Document

# This is apparently how we register a model
admin.site.register(Document)


@admin.register(Corpus)
class CorpusAdmin(admin.ModelAdmin):
    list_display = ('name', 'created_by', 'created_at')
    filter_horizontal = ('documents',)