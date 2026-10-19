# indexing.py, aggregates.py landed in Phase 5 — architecture plan §5.
# maintenance.py holds the Token staleness check and reindex_stale_documents.
#
# Explicit imports, not just this package existing: Celery's
# `app.autodiscover_tasks(["worker"])` (worker/celery_app.py) only imports
# `worker.tasks` itself, not its submodules — a @app.task in indexing.py or
# aggregates.py never registers with the running worker process without
# these imports, which surfaces as a very unhelpful `KeyError: '<task
# name>'` deep in Celery's consumer, not an import error, when the worker
# actually receives a message for a task it doesn't know about.
from . import aggregates, error_annotations, indexing, maintenance  # noqa: F401
