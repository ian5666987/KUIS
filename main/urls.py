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

    # Corpus 
    path('corpus/', views.corpus_dashboard, name='corpus_dashboard'),
    # Upload documents
    path('corpus/upload/', views.upload_document, name='upload_document'),
    # Word count
    path('corpus/frequency/<int:doc_id>/', views.word_frequency, name='word_frequency'),  
    # Collocations
    path('corpus/collocations/<int:doc_id>/', views.collocations, name='collocations'),
    # N-grams
    path('corpus/ngrams/<int:doc_id>/', views.ngrams, name='ngrams'),
    # KWIC
    path('corpus/kwic-legacy/<int:doc_id>/', views.kwic_legacy, name='kwic_legacy'),
    # Export to CSV
    path('corpus/kwic/<int:doc_id>/export/', views.kwic_export_csv, name='kwic_export_csv'),    
    # KWIC Search
    path('corpus/kwic/search/', views.kwic_search, name='kwic_search'),
]