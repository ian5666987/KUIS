"""
Error-annotation extraction + aggregation (docs/error-analytics-plan.md).
Populates ErrorAnnotation (source-of-truth occurrences) and
DocumentErrorFreq (Tier-2 aggregate, used only by the summary view) from a
document's already-uploaded content — same delete-then-insert idempotency
pattern as worker/tasks/aggregates.py, and the same two-phase bulk_create
technique main/models.py::build_tokens() uses for its self-referential
WordType resolution, here for ErrorAnnotation's self-referential `parent`
FK.

compute_document_error_freq is called from INSIDE
extract_document_error_annotations rather than as a second independent
`.delay()` from worker/tasks/indexing.py::index_document() — two
unordered `.delay()` calls give no guarantee the annotations exist before
the aggregate task reads them.
"""

from django.db import transaction
from django.db.models import Count

from main.corpus_parsing import extract_error_annotations
from main.models import Document, DocumentErrorFreq, ErrorAnnotation, ErrorTaxonomyNode
from worker.celery_app import app


@app.task
def extract_document_error_annotations(document_id: int) -> int:
    document = Document.objects.get(id=document_id)
    raw_annotations = extract_error_annotations(document.content)

    with transaction.atomic():
        ErrorAnnotation.objects.filter(document_id=document_id).delete()

        if raw_annotations:
            raw_features_set = {a['raw_features'] for a in raw_annotations}
            node_id_by_path = dict(
                ErrorTaxonomyNode.objects.filter(path__in=raw_features_set).values_list('path', 'id')
            )

            rows = [
                ErrorAnnotation(
                    document_id=document_id,
                    # Missing resolution (annotator typo, taxonomy drift)
                    # doesn't fail extraction for the rest of the document
                    # — taxonomy_node just stays null, raw_features keeps
                    # the original string for later re-resolution.
                    taxonomy_node_id=node_id_by_path.get(a['raw_features']),
                    raw_features=a['raw_features'],
                    source_segment_id=a['source_segment_id'],
                    start_position=a['start_position'],
                    end_position=a['end_position'],
                    original_text=a['original_text'],
                    correction_text=a['correction_text'],
                    state=a['state'],
                    comment=a['comment'],
                )
                for a in raw_annotations
            ]
            # Postgres bulk_create populates .pk on each returned object
            # (RETURNING id), in the same order as `rows` — pair with
            # raw_annotations directly instead of a second query, then
            # resolve `parent` in one more pass, mirroring build_tokens()'s
            # own two-phase shape for its self-referential WordType lookup.
            created = ErrorAnnotation.objects.bulk_create(rows, batch_size=5000)

            # `parent` resolves the segment's explicit parent= XML
            # attribute (not structural nesting) — real data shows these
            # can diverge, so the attribute is the authoritative signal
            # the model field is named after.
            id_by_segment_id = {
                a['source_segment_id']: obj.id
                for a, obj in zip(raw_annotations, created)
                if a['source_segment_id']
            }
            to_update = []
            for a, obj in zip(raw_annotations, created):
                parent_segment_id = a['parent_segment_id']
                if parent_segment_id and parent_segment_id in id_by_segment_id:
                    obj.parent_id = id_by_segment_id[parent_segment_id]
                    to_update.append(obj)
            if to_update:
                ErrorAnnotation.objects.bulk_update(to_update, ['parent_id'], batch_size=5000)

    count = len(raw_annotations)
    # Fire-and-forget under a real broker; synchronous under
    # CELERY_TASK_ALWAYS_EAGER=1 (tests) — same convention
    # worker/tasks/indexing.py::index_document already uses for its own
    # chained aggregate tasks.
    compute_document_error_freq.delay(document_id)
    return count


@app.task
def compute_document_error_freq(document_id: int) -> int:
    DocumentErrorFreq.objects.filter(document_id=document_id).delete()

    counts = (
        ErrorAnnotation.objects.filter(document_id=document_id, state='active', taxonomy_node__isnull=False)
        .values('taxonomy_node_id')
        .annotate(count=Count('id'))
    )
    rows = [
        DocumentErrorFreq(document_id=document_id, taxonomy_node_id=row['taxonomy_node_id'], count=row['count'])
        for row in counts
    ]
    DocumentErrorFreq.objects.bulk_create(rows, batch_size=5000)
    return len(rows)
