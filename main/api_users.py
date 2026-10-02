"""
Account management over the API (docs/user-management-plan.md): Admin
manages regular Users, Super Admin manages Admins (and, since is_staff also
covers Super Admin, gets everything the Users endpoints offer too — the
confirmed "superset" behavior, for free from ADMIN_PERMISSIONS' own is_staff
check). Super Admin accounts (is_superuser=True) are excluded from both
tiers unconditionally — not manageable through this feature at all.

One shared CRUD implementation, not two copy-pasted modules: a private
_AccountQuerysetMixin scopes every view's queryset by `is_staff_tier`, and
each tier's public view classes are two-line subclasses of a private base
that only set `is_staff_tier`/`permission_classes`. get_base_queryset() is
also the single place "wrong tier, or targeting a super-admin, or the
account is soft-deleted" all become a 404 — the same 404-not-403 shape
main/api_corpus.py::CorpusDetailView already uses for a private corpus a
non-staff user has no business knowing exists.

Delete is a soft delete (deactivate + anonymize PII, row survives) — never
a hard DB delete — because Document.user is on_delete=CASCADE, and hard-
deleting a User would cascade-delete every document (and derived Tier-1/2
analytics row) they ever uploaded.
"""

from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

from .models import UserProfile
from .permissions import ADMIN_PERMISSIONS, SUPERADMIN_PERMISSIONS, assert_not_targeting_self


class AccountPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "per_page"
    max_page_size = 100


class AccountSerializer(serializers.ModelSerializer):
    status = serializers.SerializerMethodField()
    blocked_at = serializers.SerializerMethodField()
    blocked_by = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "is_staff",
            "is_superuser",
            "date_joined",
            "last_login",
            "status",
            "blocked_at",
            "blocked_by",
        ]

    def get_status(self, obj) -> str:
        return "active" if obj.is_active else "blocked"

    def get_blocked_at(self, obj):
        profile = getattr(obj, "profile", None)
        return profile.blocked_at if profile else None

    def get_blocked_by(self, obj):
        profile = getattr(obj, "profile", None)
        return profile.blocked_by.username if profile and profile.blocked_by else None


class AccountWriteSerializer(serializers.ModelSerializer):
    """Backs both create and edit, for both tiers.

    is_active is deliberately not a field here — it only ever changes via
    the block/unblock actions below, so blocked_at/blocked_by can never
    drift out of sync with a bare PATCH. is_staff/is_superuser are never
    accepted from the request body either — they're always server-set by
    the view (from which endpoint/tier was called), the rule that prevents
    a crafted payload from self-elevating a regular user into an admin.
    """

    password = serializers.CharField(write_only=True, required=False, allow_blank=False)
    email = serializers.EmailField(required=True)  # User.email is blank=True at the model level

    class Meta:
        model = User
        fields = ["username", "email", "first_name", "last_name", "password"]

    def validate_email(self, value):
        # Mirrors main/forms.py::RegisterForm.clean_email exactly.
        existing = User.objects.filter(email__iexact=value)
        if self.instance:
            existing = existing.exclude(pk=self.instance.pk)
        if existing.exists():
            raise serializers.ValidationError("An account already uses this email address.")
        return value

    def validate_password(self, value):
        try:
            validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages)
        return value

    def validate(self, attrs):
        if self.instance is None and not attrs.get("password"):
            raise serializers.ValidationError({"password": ["This field is required."]})
        return attrs


def _blacklist_outstanding_tokens(user):
    """Block/delete must invalidate more than the next login attempt — a
    user's already-issued refresh token would otherwise still mint fresh
    access tokens via /api/auth/token/refresh/, which (unlike
    JWTAuthentication.get_user()) never re-checks is_active. Closes that
    gap immediately instead of waiting out the access token's own 15-minute
    lifetime."""
    for token in OutstandingToken.objects.filter(user=user):
        BlacklistedToken.objects.get_or_create(token=token)


class _AccountQuerysetMixin:
    is_staff_tier: bool = False  # False = Users tier, True = Admins tier

    def get_base_queryset(self):
        return (
            User.objects.filter(is_staff=self.is_staff_tier, is_superuser=False)
            .exclude(profile__deleted_at__isnull=False)
            .select_related("profile")
        )


class _AccountListView(_AccountQuerysetMixin, APIView):
    pagination_class = AccountPagination

    def get(self, request):
        queryset = self.get_base_queryset().order_by("username")

        search = request.query_params.get("search")
        if search:
            queryset = queryset.filter(
                Q(username__icontains=search)
                | Q(email__icontains=search)
                | Q(first_name__icontains=search)
                | Q(last_name__icontains=search)
            )

        status_filter = request.query_params.get("status")
        if status_filter == "active":
            queryset = queryset.filter(is_active=True)
        elif status_filter == "blocked":
            queryset = queryset.filter(is_active=False)

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, view=self)
        serializer = AccountSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    def post(self, request):
        serializer = AccountWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        password = serializer.validated_data.pop("password")
        user = User(
            is_staff=self.is_staff_tier,
            is_superuser=False,
            **serializer.validated_data,
        )
        user.set_password(password)
        user.save()
        UserProfile.objects.get_or_create(user=user)

        return Response(AccountSerializer(user).data, status=status.HTTP_201_CREATED)


class UserListView(_AccountListView):
    """GET/POST /api/users/ — Admin (and Super Admin, via is_staff)."""

    is_staff_tier = False
    permission_classes = ADMIN_PERMISSIONS


class AdminListView(_AccountListView):
    """GET/POST /api/admins/ — Super Admin only."""

    is_staff_tier = True
    permission_classes = SUPERADMIN_PERMISSIONS


class _AccountDetailView(_AccountQuerysetMixin, APIView):
    def get_object(self, pk):
        return get_object_or_404(self.get_base_queryset(), pk=pk)

    def get(self, request, pk):
        return Response(AccountSerializer(self.get_object(pk)).data)

    def patch(self, request, pk):
        target = self.get_object(pk)
        serializer = AccountWriteSerializer(target, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        password = serializer.validated_data.pop("password", None)
        for field, value in serializer.validated_data.items():
            setattr(target, field, value)
        if password:
            target.set_password(password)
        target.save()

        return Response(AccountSerializer(target).data)

    def delete(self, request, pk):
        """Soft delete: deactivate + anonymize PII, row survives (so
        Document.user and Corpus.created_by FKs stay intact). Terminal —
        excluded from get_base_queryset() afterward, so every endpoint 404s
        against this id from this point on."""
        target = self.get_object(pk)
        assert_not_targeting_self(request, target)

        now = timezone.now()
        profile, _ = UserProfile.objects.get_or_create(user=target)
        profile.deleted_at = now
        profile.deleted_by = request.user
        profile.save()

        target.is_active = False
        target.username = f"deleted-user-{target.id}"
        target.email = f"deleted-{target.id}@deleted.invalid"
        target.first_name = ""
        target.last_name = ""
        target.set_unusable_password()
        target.save()

        _blacklist_outstanding_tokens(target)
        return Response(status=status.HTTP_204_NO_CONTENT)


class UserDetailView(_AccountDetailView):
    is_staff_tier = False
    permission_classes = ADMIN_PERMISSIONS


class AdminDetailView(_AccountDetailView):
    is_staff_tier = True
    permission_classes = SUPERADMIN_PERMISSIONS


class _AccountBlockView(_AccountQuerysetMixin, APIView):
    def post(self, request, pk):
        target = get_object_or_404(self.get_base_queryset(), pk=pk)
        assert_not_targeting_self(request, target)

        target.is_active = False
        target.save()

        profile, _ = UserProfile.objects.get_or_create(user=target)
        profile.blocked_at = timezone.now()
        profile.blocked_by = request.user
        profile.save()

        _blacklist_outstanding_tokens(target)
        # target's `profile` relation was select_related() before this
        # profile row was created/updated, so its cache is stale (either
        # "no profile" if this is the first block, or the pre-update row
        # otherwise) — re-fetch so the response reflects what was just
        # written, not what get_base_queryset() saw a moment ago.
        target = self.get_base_queryset().get(pk=target.pk)
        return Response(AccountSerializer(target).data)


class _AccountUnblockView(_AccountQuerysetMixin, APIView):
    def post(self, request, pk):
        target = get_object_or_404(self.get_base_queryset(), pk=pk)

        target.is_active = True
        target.save()

        profile, _ = UserProfile.objects.get_or_create(user=target)
        profile.blocked_at = None
        profile.blocked_by = None
        profile.save()

        target = self.get_base_queryset().get(pk=target.pk)  # see UserBlockView's comment
        return Response(AccountSerializer(target).data)


class UserBlockView(_AccountBlockView):
    is_staff_tier = False
    permission_classes = ADMIN_PERMISSIONS


class AdminBlockView(_AccountBlockView):
    is_staff_tier = True
    permission_classes = SUPERADMIN_PERMISSIONS


class UserUnblockView(_AccountUnblockView):
    is_staff_tier = False
    permission_classes = ADMIN_PERMISSIONS


class AdminUnblockView(_AccountUnblockView):
    is_staff_tier = True
    permission_classes = SUPERADMIN_PERMISSIONS
