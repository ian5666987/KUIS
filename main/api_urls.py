"""
JSON API routes for KUIS-FE/FastAPI (architecture plan §3/§6) — everything
under /api/, kept separate from main/urls.py's server-rendered page routes.
Split out from config/urls.py once a second endpoint (corpora, Phase 3)
joined the three JWT routes from Phase 1; small enough to inline before
that, not anymore.
"""

from django.urls import path
from rest_framework_simplejwt.views import TokenBlacklistView, TokenRefreshView

from .api_auth import KUISTokenObtainPairView
from .api_corpus import CorpusListView

urlpatterns = [
    path("auth/token/", KUISTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("auth/token/blacklist/", TokenBlacklistView.as_view(), name="token_blacklist"),
    path("corpora/", CorpusListView.as_view(), name="api_corpus_list"),
]
