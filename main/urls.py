# This file is needed so that there will be more routing to the various pages
#  from main 'Controller'

from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('about/', views.about, name='about'),
    path('contact/', views.contact, name='contact'),

    # Restricted pages
    path('dashboard/', views.dashboard, name='dashboard'),
    path('profile/', views.profile, name='profile'),

    # Registration
    path('register/', views.register, name='register'),

    # Corpus management (creating/editing is admin-only)
    path('corpus/', views.corpus_dashboard, name='corpus_dashboard'),
    path('corpus/create/', views.corpus_create, name='corpus_create'),
    path('corpus/<int:corpus_id>/', views.corpus_detail, name='corpus_detail'),
    path('corpus/<int:corpus_id>/edit/', views.corpus_edit, name='corpus_edit'),
    path('corpus/<int:corpus_id>/delete/', views.corpus_delete, name='corpus_delete'),
    # Upload documents
    path('corpus/upload/', views.upload_document, name='upload_document'),

    # Analysis - runs over the corpora selected in the session, no doc_id.
    # Every feature has a matching /export/ route that re-runs the same query
    # with the same filters and sort, unpaginated, as CSV.
    path('analysis/', views.analysis_home, name='analysis_home'),
    # Word count
    path('analysis/frequency/', views.word_frequency, name='word_frequency'),
    path('analysis/frequency/export/', views.word_frequency_export_csv, name='word_frequency_export_csv'),
    # Collocations
    path('analysis/collocations/', views.collocations, name='collocations'),
    path('analysis/collocations/export/', views.collocations_export_csv, name='collocations_export_csv'),
    # N-grams
    path('analysis/ngrams/', views.ngrams, name='ngrams'),
    path('analysis/ngrams/export/', views.ngrams_export_csv, name='ngrams_export_csv'),
    # KWIC
    path('analysis/kwic/', views.kwic, name='kwic'),
    # Export to CSV
    path('analysis/kwic/export/', views.kwic_export_csv, name='kwic_export_csv'),
    # KWIC Search
    path('analysis/kwic/search/', views.kwic_search, name='kwic_search'),
    path('analysis/kwic/search/export/', views.kwic_search_export_csv, name='kwic_search_export_csv'),
]
