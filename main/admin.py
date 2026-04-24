from django.contrib import admin

# Register your models here.
from .models import Document

# This is apparently how we register a model
admin.site.register(Document)