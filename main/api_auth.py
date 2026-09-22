"""
JWT issuance for the Next.js frontend / FastAPI data plane (architecture
plan §3). Kept separate from views.py, which stays entirely session-cookie
based for the server-rendered pages — these two auth mechanisms don't share
code and aren't meant to.

TokenObtainPairView (from rest_framework_simplejwt) calls Django's normal
authenticate(), which walks AUTHENTICATION_BACKENDS
(config/settings.py) — main.auth_backends.UsernameOrEmailBackend runs first,
so signing in with a username or an email both work here exactly as they do
for the session-cookie login form, with no changes to that backend.
"""

from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView


class KUISTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)

        # FastAPI's dataplane/core/security.py reads is_staff straight off
        # the token (dataplane/dependencies.py::require_staff) — the same
        # is_staff-only RBAC rule main/views.py::staff_required enforces for
        # the session-cookie pages, re-expressed for a stateless verifier
        # that must not call back into Django per request.
        token['is_staff'] = user.is_staff
        token['username'] = user.username
        token['email'] = user.email

        return token


class KUISTokenObtainPairView(TokenObtainPairView):
    serializer_class = KUISTokenObtainPairSerializer
