# User Management: Admin manages Users, Super Admin manages Admins

**Status: ✅ Shipped** (backend). Frontend follows in KUIS-FE.

## Problem

Before this feature, the only way to manage accounts was Django's built-in `/admin/` site or the one-time `seed` management command. Authorization was a single `is_staff` boolean, checked independently in three places (`main/views.py::staff_required`, `main/api_corpus.py`'s `ADMIN_PERMISSIONS`, `dataplane/dependencies.py::require_staff`) — there was no "admin vs. super admin" distinction anywhere, even though `seed` already sets `is_superuser=True` on the bootstrap account; that flag was simply never read.

The ask: Admins can create/edit/(soft-)delete/block/unblock regular Users. Super Admins have all of that over Users too, plus the same five actions over Admin accounts.

## Design decisions

- **A `UserProfile` sidecar model, not a custom `AUTH_USER_MODEL`.** `django.contrib.auth.models.User` is used directly and can't be extended with new columns in place. Swapping `AUTH_USER_MODEL` 12 migrations in — with `Document.user`, `Corpus.created_by`, and `token_blacklist` already pointing at `auth.User` — would be a large, high-risk change for four extra columns. Rejected in favor of a pure-additive `OneToOneField`.
- **Profile rows are created lazily (`get_or_create`), never via a backfill migration.** A backfill would only cover rows that exist at migration time — any account created afterward (Django admin, shell) still needs the lazy path regardless, making the backfill redundant. A profile-less account (every pre-existing account, until the first time this feature touches it) simply reads as "active, never blocked."
- **Delete is a soft delete, never a hard DB delete.** `Document.user` is `on_delete=CASCADE`; hard-deleting a `User` would cascade-delete every document (and derived Tier-1/2 analytics row) they ever uploaded. Soft delete instead: deactivate (`is_active=False`), anonymize PII (username/email/names, unusable password), record `deleted_at`/`deleted_by`, and exclude the row from every endpoint going forward. The row itself, and every FK pointing at it, survives.
- **Super Admin accounts are not manageable through this feature at all.** Both the Users tier (`is_staff=False`) and the Admins tier (`is_staff=True, is_superuser=False`) unconditionally exclude `is_superuser=True` accounts from their querysets — so a request can never view, edit, block, or delete a super admin via either `/api/users/` or `/api/admins/`, regardless of who's asking. Super Admin stays seed/CLI-provisioned only.
- **`is_staff`/`is_superuser` are never accepted from the request body.** They're always server-set from which endpoint was called (`/api/users/` forces both `False`; `/api/admins/` forces `is_staff=True, is_superuser=False`). This is the rule that prevents a crafted `{"is_staff": true}` payload from self-elevating a regular user.
- **`is_active` is excluded from the general write serializer.** It only changes via the dedicated block/unblock actions, so `blocked_at`/`blocked_by` can never drift out of sync with a bare PATCH.
- **Self-action guard kept despite being structurally unreachable today.** Given the tier-exclusion rules above, an acting user's own row can never appear in the tier they're allowed to manage (an Admin's own row is never in the Users tier; a Super Admin's row is never in the Admins tier, since it's always `is_superuser=True`). `assert_not_targeting_self` is still called explicitly in the block/delete code paths as defense-in-depth against a future tier-rule change silently reopening a lockout vector.
- **Outstanding refresh tokens are blacklisted on block and delete.** `JWTAuthentication.get_user()` re-checks `is_active` on every request, so a live access token stops working immediately regardless. But `TokenRefreshView` does not re-check `is_active` — only the refresh token's own signature/expiry/blacklist status — so without this step a blocked user could still mint a fresh access token from a pre-block refresh token. Closes that gap immediately via `rest_framework_simplejwt.token_blacklist`.
- **Super Admin's superset over Users comes for free, not from a separate code path.** `/api/users/` is gated by `ADMIN_PERMISSIONS` (`is_staff`), which both Admin and Super Admin satisfy — no "Admin-or-SuperAdmin" permission class was needed.
- **One shared CRUD implementation, two thin tiers** — not two copy-pasted modules. A private `_AccountQuerysetMixin` scopes every view by `is_staff_tier`; `UserListView`/`UserDetailView`/`UserBlockView`/`UserUnblockView` and their `Admin*` counterparts are two-line subclasses of shared private base classes.

## Data model

```python
class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    blocked_at = models.DateTimeField(null=True, blank=True)
    blocked_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
```

Migration: `main/migrations/0013_userprofile.py` (`CreateModel` only).

Account state model:
- **active** — `is_active=True`, `profile.deleted_at IS NULL`.
- **blocked** — `is_active=False`, `profile.deleted_at IS NULL`. Reversible via unblock.
- **deleted** — `is_active=False`, `profile.deleted_at IS NOT NULL`. Terminal: excluded from `get_base_queryset()`, so every endpoint 404s against the id afterward.

## API

| Method | Path | Permission |
|---|---|---|
| GET/POST | `/api/users/?search=&status=&page=&per_page=` | `ADMIN_PERMISSIONS` (is_staff) |
| GET/PATCH/DELETE | `/api/users/<id>/` | `ADMIN_PERMISSIONS` |
| POST | `/api/users/<id>/block/`, `/unblock/` | `ADMIN_PERMISSIONS` |
| GET/POST | `/api/admins/?search=&status=&page=&per_page=` | `SUPERADMIN_PERMISSIONS` (is_superuser) |
| GET/PATCH/DELETE | `/api/admins/<id>/` | `SUPERADMIN_PERMISSIONS` |
| POST | `/api/admins/<id>/block/`, `/unblock/` | `SUPERADMIN_PERMISSIONS` |

Implementation: `main/permissions.py` (`IsSuperAdminUser`, `ADMIN_PERMISSIONS`, `SUPERADMIN_PERMISSIONS`, `assert_not_targeting_self`), `main/api_users.py` (serializers, shared base views, tiered subclasses), wired in `main/api_urls.py`. `main/api_corpus.py`'s own `ADMIN_PERMISSIONS` was refactored to import from `main/permissions.py` instead of defining it locally (behavior-identical — verified against the existing corpus test suite).

JWT claim: `main/api_auth.py::KUISTokenObtainPairSerializer` adds `is_superuser` alongside the existing `is_staff`/`username`/`email` — cosmetic/frontend-only, for KUIS-FE to distinguish Admin vs. Super Admin in the UI. `dataplane/` is untouched by this feature entirely.

## Rollout

1. `UserProfile` model + migration `0013`.
2. `main/permissions.py`; refactor `main/api_corpus.py` to import `ADMIN_PERMISSIONS` from it.
3. `main/api_users.py`, wired into `main/api_urls.py`.
4. `is_superuser` JWT claim in `main/api_auth.py`.
5. Tests: `UserAccountApiTests`, `AdminAccountApiTests` in `main/tests.py`.
6. This doc.
7. KUIS-FE: types/constants/claims, hooks, pages, nav.

## Explicitly deferred

- Undelete/reactivate a soft-deleted account.
- An audit view of deleted accounts.
- Admin-manages-Admin, or Super-Admin-manages-Super-Admin (out of scope per the confirmed requirements — Super Admin stays seed/CLI-only).
- Bulk actions (bulk block/delete).
- Admin-created-account welcome emails (email infra exists via `send_mail`/`DEFAULT_FROM_EMAIL`, but this feature sets the initial password directly rather than emailing a set-password link).
- `drf-spectacular`/typed codegen for these endpoints.
- A global `DEFAULT_PAGINATION_CLASS` — pagination is scoped locally to `AccountPagination` for these two list views only, so Corpus's existing unpaginated list is untouched.
