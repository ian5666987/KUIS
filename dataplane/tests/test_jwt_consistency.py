"""
The one dataplane test allowed to import Django: confirms Django's
SIMPLE_JWT['SIGNING_KEY'] and the FastAPI data plane's JWT_SECRET actually
resolve to the same value from the shared .env (architecture plan §2's
"schema drift" caution, applied to secrets instead of tables — the two
services independently read the same env var name by convention, nothing
enforces they stay equal except a test like this one).

Every other file under dataplane/tests/ deliberately avoids importing
Django, since the data plane must not depend on it at runtime — this file is
the one intentional exception, and only imports Django settings, never
Django models or the ORM.
"""

import os

import django


def test_django_and_dataplane_share_the_same_jwt_secret():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    django.setup()

    from django.conf import settings as django_settings

    from dataplane.core.config import settings as dataplane_settings

    assert django_settings.SIMPLE_JWT["SIGNING_KEY"]
    assert django_settings.SIMPLE_JWT["SIGNING_KEY"] == dataplane_settings.jwt_secret
