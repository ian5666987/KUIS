# indexing.py, aggregates.py landed in Phase 5 — architecture plan §5.
# maintenance.py (retokenize-as-a-task, content-hash staleness checks) is
# still a stub — a follow-up, not required for Phase 5's core deliverable
# (schema normalization + worker-populated aggregates + repository swap).
#
# Explicit imports, not just this package existing: Celery's
# `app.autodiscover_tasks(["worker"])` (worker/celery_app.py) only imports
# `worker.tasks` itself, not its submodules — a @app.task in indexing.py or
# aggregates.py never registers with the running worker process without
# these imports, which surfaces as a very unhelpful `KeyError: '<task
# name>'` deep in Celery's consumer, not an import error, when the worker
# actually receives a message for a task it doesn't know about.
from . import aggregates, indexing  # noqa: F401
