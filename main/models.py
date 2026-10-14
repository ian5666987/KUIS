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
    # Defaults to 'indexing', NOT 'ready': a Document row exists from the
    # moment the upload is accepted, but its tokens don't exist until
    # worker/tasks/indexing.py::index_document has run against it. The old
    # 'ready' default meant a document reported itself fully indexed the
    # instant it was inserted — before the task was even enqueued, and
    # permanently so if the Celery worker wasn't running to consume it, which
    # is exactly how a 0-token file came to show a green "Ready" badge in the
    # corpus detail page. Nothing filters on this field (it is display-only),
    # so no query's meaning changes with the default; only the honesty of
    # what's displayed does.
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_INDEXING)

    # Why a document is in the status it's in — set together with `status` and
    # only meaningful for STATUS_FAILED, where status_reason is one of
    # main/corpus_parsing.py's REASON_* codes (a stable identifier the UI can
    # key off) and status_detail is the human-readable sentence to show,
    # including specifics like an XML parse error's line/column. Both are
    # cleared on every successful index, so a document that failed once and
    # was fixed doesn't keep showing a stale explanation.
    status_reason = models.CharField(max_length=32, blank=True, default='')
    status_detail = models.TextField(blank=True, default='')

    # Integrity/reproducibility placeholders (data-processing.md §4).
    # content_hash now doubles as the upload-time dedup key (see
    # docs/data-processing.md and main/document_ingest.py) — the unique
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
    # surfaces are live at once (docs/feature-list.md §2), so both filter on
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
    speed win docs/data-processing.md's Tier 1 is about."""

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
    see docs/data-processing.md §2 / CI. Storing `word_type_ids` as a plain
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
    """The error-type tree from info/error.xml (docs/feature-list.md).
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
    source-of-truth occurrence table (docs/feature-list.md),
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
    ErrorAnnotation directly instead — see docs/feature-list.md
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


class UserProfile(models.Model):
    """Sidecar for the four extra fields user management needs on top of
    django.contrib.auth.models.User (docs/feature-list.md) — a
    OneToOneField instead of a custom AUTH_USER_MODEL, since swapping that
    this late would touch every existing FK to auth.User (Document.user,
    Corpus.created_by, token_blacklist) for a net gain of four columns.

    Rows are created lazily (get_or_create) the first time an account is
    blocked/deleted, plus eagerly whenever main/api_users.py creates a new
    account — never via a one-time backfill migration, since accounts
    created afterward (Django admin, shell) would still need the lazy path
    regardless, making a backfill redundant. A profile-less account (every
    account that predates this feature and has never been touched by it)
    simply reads as "active, never blocked".
    """

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    blocked_at = models.DateTimeField(null=True, blank=True)
    blocked_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    def __str__(self):
        return f"profile({self.user.username})"


class DocumentMetadata(models.Model):
    """One row per catalogue entry from metadata/*.csv — NOT one per Document
    (docs/data-processing.md).

    The catalogue is the authority and is loaded independently of upload
    order: all ~1,500 rows can exist before a single file is uploaded, and a
    file can be uploaded before its catalogue row exists. So `document` is
    nullable and resolved lazily from whichever side appears second — at
    upload time by main/document_ingest.py::get_or_create_document, and in
    reverse by the load_metadata_catalogue command.

    The FK lives here rather than as a sidecar column on Document for two
    reasons: Document's table stays untouched (nothing to re-mirror in
    dataplane/models/tables.py's `document`), and OneToOneField enforces at
    DB level that two catalogue entries can't claim the same Document —
    which matters, because uniq_document_content_hash collapses
    byte-identical uploads into a single row that several catalogue
    filenames could otherwise point at.

    Every analysis field is nullable: the filter's "(no value)" option has
    to match an entry whose field is empty exactly as it matches a document
    with no entry at all, and a LEFT OUTER JOIN makes NULL mean both.
    """

    # The CSV's "File name" verbatim (e.g. 'TUFS2023KOMSHI314.txt'), kept for
    # reporting/traceability. Deliberately NOT unique — match_key is the real
    # constraint, and two source rows differing only by extension would
    # collide there first.
    source_filename = models.CharField(max_length=200)
    # normalize_match_key() of the above: basename, one extension stripped,
    # lowercased. Stored rather than derived at query time so the join to a
    # document is a plain indexed equality.
    match_key = models.CharField(max_length=200, unique=True)
    # SET_NULL, not CASCADE: documents are never hard-deleted today
    # (docs/feature-list.md), and if one ever is, the catalogue row
    # is source data that should outlive it.
    document = models.OneToOneField(
        Document,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='catalogue_entry',
    )

    university = models.CharField(max_length=50, null=True, blank=True)
    year = models.IntegerField(null=True, blank=True)
    grade = models.IntegerField(null=True, blank=True)
    # topic / topic_en are two independent columns, never a normalized pair:
    # the source mapping is not 1:1 (both "notable figure" and "public
    # figure" appear for 'tokoh'; both 'tari' and 'tarian' for "dance").
    topic = models.CharField(max_length=100, null=True, blank=True)
    topic_en = models.CharField(max_length=100, null=True, blank=True)
    # The catalogue's own word count — a third, independent notion of length
    # alongside Document.token_count/token_count_corrected, which are
    # computed by main/corpus_parsing.py's own tokenizer and legitimately
    # disagree with this. Stored, not reconciled, and not filterable.
    word_count = models.IntegerField(null=True, blank=True)
    name_code = models.CharField(max_length=50, null=True, blank=True)

    # The person who wrote this file, resolved from (university, name_code)
    # against the survey CSVs (docs/data-processing.md). Nullable for the
    # same reason `document` is, and then some: four of the seven universities
    # (APU, CHU, SFC, SOP) have no survey data at all, so ~53% of the
    # catalogue is legitimately author-less. NULL here is what the filter's
    # "(not specified)" option selects, via the same LEFT OUTER JOIN that
    # already makes a missing catalogue row and an empty field equivalent.
    #
    # SET_NULL rather than CASCADE, matching `document` above: the catalogue
    # row is source data that should outlive a deleted author.
    #
    # Resolved on (university, name_code) TOGETHER — never name_code alone.
    # See Author's docstring: name_code collides across universities and a
    # one-sided key mis-assigns 8 real files.
    author = models.ForeignKey(
        'Author',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='documents',
    )

    source_file = models.CharField(max_length=100)
    imported_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = 'document metadata'
        ordering = ['source_filename']
        indexes = [
            models.Index(fields=['university']),
            models.Index(fields=['year', 'grade']),
            models.Index(fields=['name_code']),
            models.Index(fields=['author']),
        ]

    def __str__(self):
        return self.source_filename


class Author(models.Model):
    """The person who wrote the essays — one row per (university, name_code),
    built from the person-level survey CSVs in metadata/authors/
    (docs/data-processing.md).

    A separate table from DocumentMetadata because the grain is different:
    the catalogue has one row per FILE, the surveys have one row per PERSON
    per survey occasion. ~235 people wrote ~735 of the catalogued files.

    `unique_together` on (university, name_code) rather than name_code alone
    is load-bearing, not defensive. name_code is NOT unique across
    universities in the real catalogue: RAKA is both an OU and a CHU student,
    NAMI both KUIS and APU, and five more besides. Five of those collisions
    have survey data under exactly one of their two universities, so a
    name_code-only key would silently hand 8 files another person's
    demographics. The university is part of the identity.

    ONLY the traits the filter actually exposes live here — gender, plus the
    mother tongues in the child table below. Nothing else from the 20-column
    source CSVs is imported.

    That is deliberate, and it is the second reason the rest was never worth
    a column: age, learning duration and the difficulty self-rating all
    change between a person's surveys (they are surveyed twice per academic
    year, and a filename records only year and grade, never which survey), so
    no single value of any of them can honestly be attached to a file. They
    were briefly stored unread, which is just a second copy of the CSV that
    can go stale. The CSVs are the authority and are tracked in git, so if
    the client later picks a rule for one of those traits, re-importing is
    the whole cost of getting it back.
    """

    university = models.CharField(max_length=50)
    name_code = models.CharField(max_length=50)

    # Derived across all of a person's survey rows, which agree on it in all
    # but one case.
    # NULL when they disagree: a conflict resolves to "unknown", never to a
    # guess, and the importer reports it for the client to correct at source.
    gender = models.CharField(max_length=20, null=True, blank=True)
    # The source string verbatim, so a normalisation bug is always traceable
    # back to what the CSV actually said — the same raw-plus-parsed pairing
    # AuthorMotherTongue.tongue_raw keeps.
    gender_raw = models.CharField(max_length=100, blank=True, default='')

    # Set when a person's surveys disagree on anything invariant. Reported by
    # the importer and surfaced in the admin; it never changes how the filter
    # behaves, it just stops a conflict from being invisible.
    has_conflict = models.BooleanField(default=False)
    conflict_note = models.TextField(blank=True, default='')

    source_file = models.CharField(max_length=100)
    imported_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['university', 'name_code'],
                name='uniq_author_university_name_code',
            ),
        ]
        ordering = ['university', 'name_code']
        indexes = [
            models.Index(fields=['gender']),
        ]

    def __str__(self):
        return f'{self.university}/{self.name_code}'


class AuthorMotherTongue(models.Model):
    """One mother tongue of one author — a child table, not a column, because
    a person can have more than one (TUFS/NEGSEI has Japanese and Malay).

    Populated as the UNION of every tongue across that person's surveys.
    Union is the right resolution precisely because it invents nothing: every
    value in it was asserted by some survey of that person. Resolving a
    set-level disagreement to NULL instead would have made Malay vanish from
    the facet entirely over one inconsistent row, taking 21 filterable files
    with it.

    The filter reads this with EXISTS rather than a join (see
    main/metadata_catalogue.py::_facet_q and
    dataplane/repositories/shared.py::_metadata_conditions). A join would
    multiply rows, and _analysis_context sums token_count over that same
    queryset, so a two-tongue document would be counted twice.
    """

    author = models.ForeignKey(
        Author,
        on_delete=models.CASCADE,
        related_name='mother_tongues',
    )
    tongue = models.CharField(max_length=50)
    tongue_raw = models.CharField(max_length=200, blank=True, default='')

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['author', 'tongue'],
                name='uniq_author_mother_tongue',
            ),
        ]
        ordering = ['author', 'tongue']
        indexes = [
            models.Index(fields=['tongue']),
        ]

    def __str__(self):
        return f'{self.author}: {self.tongue}'


def build_tokens(document):
    """(Re)computes the Token rows for a document from its content, for both the
    original and corrected word streams. Does not save the document itself.

    Resolves each distinct word to a WordType row (bulk get-or-create, one
    query for the missing forms rather than one per word) before building
    Token rows against word_type_id — architecture plan §2's Tier 1
    normalization, plus docs/data-processing.md defects #1/#2 (unbounded
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
# (docs/data-processing.md Tier 3: "Replace the post_save signal with an
# explicit index_document() call"). See docs/CHANGELOG.md's Phase 5
# status entry for why this moved here instead of staying implicit.
