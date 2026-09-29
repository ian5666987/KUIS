# Models seem to connect to database

from django.db import models, transaction
from django.contrib.auth.models import User

from .corpus_parsing import extract_word_streams

# Create your models here.
class Document(models.Model):
    STATUS_INDEXING = 'indexing'
    STATUS_READY = 'ready'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = [
        (STATUS_INDEXING, 'Indexing'),
        (STATUS_READY, 'Ready'),
        (STATUS_FAILED, 'Failed'),
    ]

    title = models.CharField(max_length=200)
    content = models.TextField()
    uploaded_at = models.DateTimeField(auto_now_add=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)

    # Preprocessed
    token_count = models.IntegerField(default=0)
    token_count_corrected = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    # Architecture plan §5/§8 Phase 5 — the worker sets these explicitly
    # (worker/tasks/indexing.py), replacing the synchronous post_save signal
    # this model used to build tokens with. Existing documents backfilled to
    # 'ready' by main/management/commands/backfill_tier2.py.
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_READY)

    # Integrity/reproducibility placeholders (optimisation_plan.md §5).
    # content_hash now doubles as the upload-time dedup key (see
    # docs/content-hash-dedup.md and main/document_ingest.py) — the unique
    # constraint below enforces it at the DB level. tokenized_hash tracks
    # what the current Token rows were actually built from; a future
    # `tokenized_hash IS DISTINCT FROM content_hash` staleness check remains
    # a separate, still-open item.
    content_hash = models.CharField(max_length=64, null=True, blank=True)
    tokenized_hash = models.CharField(max_length=64, null=True, blank=True)
    tokenizer_version = models.IntegerField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'uploaded_at']),
        ]
        constraints = [
            models.UniqueConstraint(fields=['content_hash'], name='uniq_document_content_hash'),
        ]

    def __str__(self):
        return self.title

class Corpus(models.Model):
    name = models.CharField(max_length=200, unique=True)
    description = models.TextField(blank=True)
    documents = models.ManyToManyField(Document, related_name='corpora', blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # Admin-only sharing toggle: public corpora are visible to every
    # registered user (the behavior every corpus already had before this
    # field existed, hence default=True — flipping it on doesn't change
    # any existing corpus's visibility). Private corpora are visible only
    # to staff. Enforced in main/views.py (corpus_dashboard/corpus_detail)
    # and main/api_corpus.py (CorpusListView/CorpusDetailView) — both
    # surfaces are live at once (docs/ONBOARDING.md §1), so both filter on
    # this the same way rather than just one of them.
    is_public = models.BooleanField(
        default=True,
        help_text='Public corpora are visible to every registered user. Private corpora are visible only to admins.',
    )

    class Meta:
        verbose_name_plural = 'corpora'
        ordering = ['name']

    def __str__(self):
        return self.name


class WordType(models.Model):
    """The distinct surface forms across the whole corpus (architecture plan
    §2, Tier 1). Heaps' law means this grows roughly as sqrt(tokens) — a
    10M-token corpus has perhaps 200-300k distinct forms, so Token storing
    an int FK here instead of repeating the varchar is the actual space/
    speed win optimisation_plan.md's Tier 1 is about."""

    form = models.CharField(max_length=100, unique=True)

    def __str__(self):
        return self.form


class Token(models.Model):
    ORIGINAL = 'original'
    CORRECTED = 'corrected'
    MODE_CHOICES = [
        (ORIGINAL, 'Original'),
        (CORRECTED, 'Corrected'),
    ]

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='tokens')
    # Replaces the old `word` varchar column (architecture plan §2, Tier 1
    # — "Token.word_type FK replacing Token.word"). Every reader of the old
    # `word` field was found and updated in the same change: this model's
    # own build_tokens()/__str__, main/views.py's fast-KWIC queries
    # (_word_index_matches/_hydrate_word_index), and every dataplane
    # repository that touched Token.word directly.
    word_type = models.ForeignKey(WordType, on_delete=models.CASCADE, related_name='tokens')
    position = models.IntegerField()
    mode = models.CharField(max_length=10, choices=MODE_CHOICES, default=ORIGINAL)

    class Meta:
        indexes = [
            models.Index(fields=['document', 'mode', 'position']),
        ]

    def __str__(self):
        return f"{self.word_type.form} ({self.document_id}:{self.mode}:{self.position})"


class DocumentWordFreq(models.Model):
    """Precomputed per-document word counts (architecture plan §2, Tier 2)
    — COMPLETE, not top-K (vocabulary size is bounded; unlike n-grams there
    is no combinatorial tail worth truncating). A multi-corpus word-
    frequency query becomes `SUM(count) GROUP BY word_type_id WHERE
    document_id IN (...)` instead of scanning raw tokens."""

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='word_freqs')
    word_type = models.ForeignKey(WordType, on_delete=models.CASCADE)
    mode = models.CharField(max_length=10, choices=Token.MODE_CHOICES)
    count = models.IntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['document', 'word_type', 'mode'], name='uniq_doc_wordfreq'),
        ]
        indexes = [
            models.Index(fields=['document', 'mode']),
        ]


class DocumentNgram(models.Model):
    """Precomputed per-document n-gram counts — TOP-K per (document, n,
    mode) with a frequency floor, not every n-gram (architecture plan §2,
    Tier 2: "Storing all 5-grams is storing the corpus again at 5x
    multiplicity, and the tail is never read"). The worker task
    (worker/tasks/aggregates.py) decides K and the floor, not this schema —
    see TOP_K_PER_DOCUMENT / FREQUENCY_FLOOR there.

    Simplification vs. the architecture plan's original design: the plan
    described a separate `Ngram` dimension table keyed by an ArrayField of
    word_type ids, deduping the n-gram's identity across documents.
    ArrayField is Postgres-only, and this project's Django test suite (and
    CI's `django-tests` job) deliberately runs against SQLite for speed —
    see docs/ONBOARDING.md §6/CI. Storing `word_type_ids` as a plain
    cross-database JSONField directly on this table avoids that hard
    dependency, at the cost of repeating the same id sequence once per
    document instead of once globally — a fine trade at the corpus sizes
    this project actually has today, and revisitable if scale ever
    justifies the extra table."""

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='ngram_freqs')
    n = models.SmallIntegerField()
    word_type_ids = models.JSONField()  # ordered list[int], e.g. [12, 45, 7]
    mode = models.CharField(max_length=10, choices=Token.MODE_CHOICES)
    count = models.IntegerField()

    class Meta:
        indexes = [
            models.Index(fields=['document', 'n', 'mode']),
        ]


class ErrorTaxonomyNode(models.Model):
    """The error-type tree from info/error.xml (docs/error-analytics-plan.md).
    Reference data, not user data — loaded/reloaded wholesale by the
    `load_error_taxonomy` management command, never edited row-by-row.

    `path` is stored in the exact semicolon-joined format the corpus XML's
    `<segment features=...>` attribute uses ("eror;gramatikal;frasa-nomina;
    nomafx"), so resolving an annotation's raw_features to a node is a
    single indexed equality lookup, and category/subcategory filtering is a
    substring-containment check against this same column — see
    dataplane/repositories/postgres/_error_query.py. This format is
    load-bearing for that lookup and must not change independently of the
    XML it mirrors."""

    code = models.CharField(max_length=50, unique=True)
    parent = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='children')
    path = models.CharField(max_length=255, unique=True)
    gloss = models.TextField(blank=True)
    is_leaf = models.BooleanField(default=False)
    # Depth-1 ancestor's code (e.g. "gramatikal"), set by load_error_taxonomy
    # at load time — the summary view's grouping key, computed once here
    # rather than re-derived from `path` on every query.
    top_category = models.CharField(max_length=50, null=True, blank=True)

    def __str__(self):
        return self.code


class ErrorAnnotation(models.Model):
    """One row per <segment features=...> in a document's content — the
    source-of-truth occurrence table (docs/error-analytics-plan.md),
    extracted by worker/tasks/error_annotations.py via
    main/corpus_parsing.py::extract_error_annotations. `taxonomy_node` is
    nullable and `raw_features` is kept verbatim so an unresolvable code
    (annotator typo, taxonomy drift) doesn't fail extraction for the rest
    of the document. `parent` mirrors the segment's own `parent=` XML
    attribute (nested segments represent overlapping errors), not
    structural nesting — the two can diverge in real data."""

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='error_annotations')
    taxonomy_node = models.ForeignKey(ErrorTaxonomyNode, on_delete=models.SET_NULL, null=True, blank=True, related_name='annotations')
    raw_features = models.CharField(max_length=255)
    parent = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='children')
    source_segment_id = models.CharField(max_length=50, blank=True)
    start_position = models.IntegerField()
    end_position = models.IntegerField()
    original_text = models.TextField()
    correction_text = models.TextField(blank=True)
    state = models.CharField(max_length=20, default='active')
    comment = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['document', 'taxonomy_node']),
            models.Index(fields=['document', 'start_position']),
        ]

    def __str__(self):
        return f"{self.raw_features} ({self.document_id}:{self.start_position}-{self.end_position})"


class DocumentErrorFreq(models.Model):
    """Precomputed per-document error-type counts, mirroring DocumentWordFreq
    — used only by the summary view (SUM ... GROUP BY top_category).
    Error Frequency (grouping by the erroneous phrase's own text) reads
    ErrorAnnotation directly instead — see docs/error-analytics-plan.md
    Part 3 for why: unlike vocabulary, distinct phrase text is not a
    bounded-enough dimension for this aggregate to meaningfully shrink."""

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='error_freqs')
    taxonomy_node = models.ForeignKey(ErrorTaxonomyNode, on_delete=models.CASCADE)
    count = models.IntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['document', 'taxonomy_node'], name='uniq_doc_errorfreq'),
        ]
        indexes = [
            models.Index(fields=['document']),
        ]


def build_tokens(document):
    """(Re)computes the Token rows for a document from its content, for both the
    original and corrected word streams. Does not save the document itself.

    Resolves each distinct word to a WordType row (bulk get-or-create, one
    query for the missing forms rather than one per word) before building
    Token rows against word_type_id — architecture plan §2's Tier 1
    normalization, plus optimisation_plan.md defects #1/#2 (unbounded
    bulk_create, no transaction), fixed together since both needed touching
    this same function anyway.
    """
    original_words, corrected_words = extract_word_streams(document.content)
    all_words = set(original_words) | set(corrected_words)

    with transaction.atomic():
        existing = {
            wt.form: wt.id
            for wt in WordType.objects.filter(form__in=all_words)
        }
        missing = all_words - existing.keys()
        if missing:
            WordType.objects.bulk_create(
                [WordType(form=w) for w in missing], batch_size=5000, ignore_conflicts=True
            )
            # ignore_conflicts means bulk_create can't return the new ids
            # (a concurrent index_document for a different document may have
            # raced to create the same WordType) — re-fetch instead of
            # trusting bulk_create's return value.
            existing.update(
                WordType.objects.filter(form__in=missing).values_list('form', 'id')
            )

        tokens = [
            Token(document=document, word_type_id=existing[word], position=i, mode=Token.ORIGINAL)
            for i, word in enumerate(original_words)
        ] + [
            Token(document=document, word_type_id=existing[word], position=i, mode=Token.CORRECTED)
            for i, word in enumerate(corrected_words)
        ]

        Token.objects.bulk_create(tokens, batch_size=5000)

    return len(original_words), len(corrected_words)


# NOTE: the post_save signal that used to call build_tokens() synchronously
# on every Document creation is REMOVED as of architecture plan Phase 5 —
# indexing is now explicitly triggered via worker/tasks/indexing.py
# ::index_document(), called from main/views.py's upload/create views
# (optimisation_plan.md Tier 3: "Replace the post_save signal with an
# explicit index_document() call"). See docs/new-architecture.md's Phase 5
# status entry for why this moved here instead of staying implicit.
