"""
Celery worker — preprocessing, tokenization, statistics (architecture plan
§5). Bootstraps Django exactly like manage.py does, so tasks can import
main.models directly and reuse build_tokens() rather than re-implementing
tokenization against a second ORM (architecture plan §1's "who writes"
rule — the worker is the only writer of Token/aggregate tables, always
through Django's ORM).

Phase 5: worker/tasks/{indexing,aggregates}.py are real. maintenance.py
holds the Token staleness check and the reindex_stale_documents task, which
celery beat dispatches on the schedule at the bottom of this file.

Run locally with: celery -A worker.celery_app worker --pool=solo --loglevel=info
and, for the staleness sweep:  celery -A worker.celery_app beat --loglevel=info
"""

import os
from datetime import timedelta

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

# Standard Celery testing pattern: with this on, .delay()/.apply_async()
# execute synchronously in-process instead of enqueuing to Redis. Set by
# main/tests.py and dataplane/tests/conftest.py's test env (CELERY_TASK_
# ALWAYS_EAGER=1) so test fixtures exercise the REAL indexing task —
# tokenize + hash + aggregate — rather than a separate hand-rolled test-only
# setup path that could drift from what production actually does.
if os.environ.get("CELERY_TASK_ALWAYS_EAGER") == "1":
    app.conf.task_always_eager = True
    app.conf.task_eager_propagates = True

# Read only by `celery beat` (the kuis-beat service in docker-compose.yml),
# which enqueues; the worker runs the sweep. Each sweep re-hashes every
# document's content — about 1.2 MB of text for the whole corpus today
# (docs/hosting.md) — so hourly costs little, and is soon enough for an admin
# edit, or a TOKENIZER_VERSION bump after a deploy, to be picked up.
# `expires` drops a sweep still waiting when the next one is due: after hours
# with the worker down it runs one sweep, not one per missed hour, each
# re-queuing the same documents.
STALE_SWEEP_INTERVAL_SECONDS = int(os.environ.get("STALE_SWEEP_INTERVAL_SECONDS", "3600"))
app.conf.beat_schedule = {
    "reindex-stale-documents": {
        "task": "worker.tasks.maintenance.reindex_stale_documents",
        "schedule": timedelta(seconds=STALE_SWEEP_INTERVAL_SECONDS),
        "options": {"expires": STALE_SWEEP_INTERVAL_SECONDS},
    },
}
