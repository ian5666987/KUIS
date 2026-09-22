"""
Celery worker — preprocessing, tokenization, statistics (architecture plan
§5). Bootstraps Django exactly like manage.py does, so tasks can import
main.models directly and reuse build_tokens() rather than re-implementing
tokenization against a second ORM (architecture plan §1's "who writes"
rule — the worker is the only writer of Token/aggregate tables, always
through Django's ORM).

PHASE 0: zero tasks registered. worker/tasks/{indexing,aggregates,
maintenance}.py land in Phase 5, replacing main/models.py's synchronous
post_save signal.

Run locally with: celery -A worker.celery_app worker --loglevel=info
"""

import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from celery import Celery  # noqa: E402  (after django.setup(), per Django+Celery convention)

# config/settings.py already ran environ.Env.read_env(BASE_DIR / '.env') as a
# side effect of django.setup() above, which populates os.environ — no need
# for a second django-environ instance here.
redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

app = Celery("kuis", broker=redis_url, backend=redis_url)
app.autodiscover_tasks(["worker"])
