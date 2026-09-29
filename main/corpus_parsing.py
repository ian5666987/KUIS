import re
import xml.etree.ElementTree as ET

WORD_RE = re.compile(r"\b\w+\b")


def _tokenize(text):
    if not text:
        return []
    return WORD_RE.findall(text.lower())


def _flat_tokenize_both(content):
    words = _tokenize(content)
    return words, list(words)


def _walk_body(body, original_words, corrected_words, annotations=None):
    """Single recursive walk covering both the token streams and (when
    `annotations` is a list) error-annotation extraction, so a segment's
    ErrorAnnotation.start_position/end_position always land exactly where
    the original-stream Token.position for that span will — by
    construction, not by two independently-maintained walkers that could
    drift (docs/error-analytics-plan.md).

    Fixes the historical bug where only direct children of <body> were
    visited: a <segment> nested inside another <segment> (the data's way of
    representing overlapping errors — e.g. a phrase-level word-order error
    containing a word-level spelling error, see
    info/KUIS2023FUA201-Eror.xml) used to contribute nothing to either
    stream. Retokenizing real documents after this change shifts token
    counts/positions for any document that has nested segments — expected,
    see the retokenize --dry-run step in docs/error-analytics-plan.md.
    """

    def add_both(text):
        words = _tokenize(text)
        original_words.extend(words)
        corrected_words.extend(words)

    def add_original(text):
        original_words.extend(_tokenize(text))

    def record_annotation(segment, start, end):
        if annotations is None:
            return
        annotations.append({
            'source_segment_id': segment.get('id', ''),
            'parent_segment_id': segment.get('parent', ''),
            'raw_features': segment.get('features', ''),
            'start_position': start,
            'end_position': end,
            'original_text': ''.join(segment.itertext()),
            # Read case-insensitively here (unlike the corrected token
            # stream below) — this only affects display text, not token
            # generation, and real data uses both castings inconsistently
            # on sibling segments (info/KUIS2023FUA201-Eror.xml has both
            # `Correction=` and `correction=`).
            'correction_text': segment.get('Correction') or segment.get('correction') or '',
            'state': segment.get('state', 'active'),
            'comment': segment.get('comment', ''),
        })

    def walk_segment(node):
        """Inside a <segment>: text (including nested segments' text) feeds
        the ORIGINAL stream only — mirrors the pre-fix rule that a
        segment's own text never touches the corrected stream, just
        extended to actually reach nested content instead of silently
        dropping it. The corrected stream is fed exclusively by the
        OUTERMOST segment's own Correction value (added once, by `walk`
        below) — a nested segment's own Correction is still captured on its
        ErrorAnnotation row for display, but the outer segment's correction
        already stands in for the whole span in the corrected reading, so
        it is not added again here."""
        add_original(node.text)
        for child in node:
            if child.tag == 'segment':
                start = len(original_words)
                walk_segment(child)
                record_annotation(child, start, len(original_words))
            else:
                add_original(''.join(child.itertext()))
            add_original(child.tail)

    def walk(node):
        add_both(node.text)
        for child in node:
            if child.tag == 'segment':
                start = len(original_words)
                walk_segment(child)
                record_annotation(child, start, len(original_words))
                # UNCHANGED: capital 'Correction' only, top-level segments
                # only — the one rule already verified pre-fix, kept
                # exactly as-is (docs/error-analytics-plan.md).
                corrected_words.extend(_tokenize(child.get('Correction', '')))
            else:
                walk(child)
            add_both(child.tail)

    walk(body)


def looks_like_structured_xml(content):
    """True when `content` parses as XML with a <body> element — corpus_parsing's
    own definition of "structured" input. Extracted so main/document_ingest.py's
    hash canonicalization uses the identical check tokenization does here, rather
    than a second implementation that could disagree with this one."""
    if not content:
        return False
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return False
    return root.find('body') is not None


def parse_document(content):
    """Returns (original_words, corrected_words, is_structured).

    `is_structured` is False when the content isn't parseable corpus XML with a
    <body>, in which case both streams are the same flat tokenization and the
    "corrected" reading carries no extra information. Callers surface that to the
    user rather than letting the two modes look identical for no visible reason.
    """
    if not looks_like_structured_xml(content):
        return (*_flat_tokenize_both(content), False)

    root = ET.fromstring(content)  # re-parse: looks_like_structured_xml already
                                    # proved this succeeds; simpler than having
                                    # the predicate also return the parsed tree.
    body = root.find('body')

    original_words = []
    corrected_words = []
    _walk_body(body, original_words, corrected_words)

    return original_words, corrected_words, True


def extract_word_streams(content):
    """Returns (original_words, corrected_words) tokenized from a document's content.

    For well-formed corpus XML with a <body>, plain text contributes equally to both
    streams, and each <segment> contributes its inner text (nested segments included)
    to the original stream and its Correction attribute (if non-empty) to the
    corrected stream. Falls back to flat word tokenization of the raw content
    (identical for both streams) when the content isn't parseable XML with a <body>
    element.
    """
    original_words, corrected_words, _ = parse_document(content)

    return original_words, corrected_words


def extract_error_annotations(content):
    """Returns a list of raw annotation dicts (source_segment_id,
    parent_segment_id, raw_features, start_position, end_position,
    original_text, correction_text, state, comment) — one per <segment
    features=...> in `content`, nested or not. Empty for non-structured
    content (no <body>).

    Uses the same recursive walk as extract_word_streams, so
    start_position/end_position land exactly where Token.position (mode=
    original) will, by construction (docs/error-analytics-plan.md)."""
    if not looks_like_structured_xml(content):
        return []

    root = ET.fromstring(content)
    body = root.find('body')

    original_words = []
    corrected_words = []
    annotations = []
    _walk_body(body, original_words, corrected_words, annotations=annotations)

    return annotations
