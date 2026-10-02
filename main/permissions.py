"""
Shared DRF permission classes for the JSON API (docs/user-management-plan.md).

ADMIN_PERMISSIONS used to be defined locally in main/api_corpus.py; moved
here so main/api_users.py can reuse the exact same is_staff gate for the
Users tier (Admin and Super Admin both have is_staff=True, so this is also
how Super Admin gets the confirmed "superset over Users" behavior for
free) without importing one API module from another.
"""

from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission, IsAdminUser, IsAuthenticated


class IsSuperAdminUser(BasePermission):
    """Gates the Admins tier (main/api_users.py) — is_superuser, not just
    is_staff. Mirrors IsAdminUser's own shape/is_active check."""

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_active and request.user.is_superuser)


# Stacking IsAuthenticated first preserves 401-for-anonymous instead of
# falling through to a bare 403 — same reasoning main/api_corpus.py's
# original ADMIN_PERMISSIONS docstring gave.
ADMIN_PERMISSIONS = [IsAuthenticated, IsAdminUser]
SUPERADMIN_PERMISSIONS = [IsAuthenticated, IsSuperAdminUser]


def assert_not_targeting_self(request, target_user):
    """Nobody can block/delete their own currently-authenticated account
    through this feature — cheap defense-in-depth against an accidental
    lockout, kept even though today's tier-exclusion rules (an acting
    user's own row is never in the tier they're allowed to manage) already
    make this unreachable end-to-end (docs/user-management-plan.md)."""
    if target_user.pk == request.user.pk:
        raise PermissionDenied("You cannot perform this action on your own account.")
