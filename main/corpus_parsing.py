import re
import xml.etree.ElementTree as ET

WORD_RE = re.compile(r"\b\w+\b")


def _tokenize(text):
    if not text:
        return []
    return WORD_RE.findall(text.lower())


def _walk_body(body, words, annotations=None):
    """Single recursive walk covering both the token stream and (when
    `annotations` is a list) error-annotation extraction, so a segment's
    ErrorAnnotation.start_position/end_position always land exactly where
    the Token.position for that span will — by construction, not by two
    independently-maintained walkers that could drift
    (docs/feature-list.md).

    Only the text as written is tokenized: a <segment> contributes its
    inner text, nested segments included. Its Correction attribute is not
    tokenized (the separate corrected stream was dropped to save space) but
    is still captured on the segment's ErrorAnnotation row for display.

    Fixes the historical bug where only direct children of <body> were
    visited: a <segment> nested inside another <segment> (the data's way of
    representing overlapping errors — e.g. a phrase-level word-order error
    containing a word-level spelling error, see
    info/KUIS2023FUA201-Eror.xml) used to contribute nothing to the
    stream. Retokenizing real documents after this change shifts token
    counts/positions for any document that has nested segments — expected,
    see the retokenize --dry-run step in docs/feature-list.md.
    """

    def add(text):
        words.extend(_tokenize(text))

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
            # Read case-insensitively: real exports spell this attribute
            # both ways, consistently within a file (info/KUIS2022NAMI3.xml
            # uses `Correction=`, info/OU 2023 Beta 201.xml `correction=`),
            # and info/KUIS2023FUA201-Eror.xml mixes them on siblings.
            'correction_text': segment.get('Correction') or segment.get('correction') or '',
            'state': segment.get('state', 'active'),
            'comment': segment.get('comment', ''),
        })

    def walk_segment(node):
        add(node.text)
        for child in node:
            if child.tag == 'segment':
                start = len(words)
                walk_segment(child)
                record_annotation(child, start, len(words))
            else:
                add(''.join(child.itertext()))
            add(child.tail)

    def walk(node):
        add(node.text)
        for child in node:
            if child.tag == 'segment':
                start = len(words)
                walk_segment(child)
                record_annotation(child, start, len(words))
            else:
                walk(child)
            add(child.tail)

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


def extract_words(content):
    """Returns the words of a document's content, as written.

    For well-formed corpus XML with a <body>, plain text and every <segment>'s
    inner text (nested segments included) are tokenized in document order.
    Falls back to flat word tokenization of the raw content when it isn't
    parseable XML with a <body> element.
    """
    if not looks_like_structured_xml(content):
        return _tokenize(content)

    root = ET.fromstring(content)  # re-parse: looks_like_structured_xml already
                                    # proved this succeeds; simpler than having
                                    # the predicate also return the parsed tree.
    words = []
    _walk_body(root.find('body'), words)
    return words


def extract_error_annotations(content):
    """Returns a list of raw annotation dicts (source_segment_id,
    parent_segment_id, raw_features, start_position, end_position,
    original_text, correction_text, state, comment) — one per <segment
    features=...> in `content`, nested or not. Empty for non-structured
    content (no <body>).

    Uses the same recursive walk as extract_words, so
    start_position/end_position land exactly where Token.position will,
    by construction (docs/feature-list.md)."""
    if not looks_like_structured_xml(content):
        return []

    root = ET.fromstring(content)
    annotations = []
    _walk_body(root.find('body'), [], annotations=annotations)

    return annotations


# --- Upload diagnostics ----------------------------------------------------
#
# Why this lives here rather than in the worker: the parsing rules a document
# has to satisfy ARE this module's rules, so the explanation of why a document
# failed them belongs next to the code enforcing them. worker/tasks/indexing.py
# only decides what to do with the verdict (mark the document failed, skip the
# aggregates); it never re-derives it.
#
# Codes are stable identifiers stored on Document.status_reason — the UI may
# key styling/help links off them, so treat them like an API: add new ones
# freely, don't repurpose existing ones.

REASON_EMPTY_CONTENT = 'empty_content'
REASON_XML_MALFORMED = 'xml_malformed'
REASON_XML_NO_BODY = 'xml_no_body'
REASON_NO_TOKENS = 'no_tokens'
REASON_WORKER_ERROR = 'worker_error'
REASON_NEVER_INDEXED = 'never_indexed'

REASON_MESSAGES = {
    REASON_EMPTY_CONTENT: 'The file is empty — there is no text to index.',
    REASON_XML_MALFORMED: "The file looks like corpus XML but isn't well-formed, so it can't be parsed.",
    REASON_XML_NO_BODY: 'The XML parsed, but it has no <body> element, so there is no text to index.',
    REASON_NO_TOKENS: 'The file parsed, but no words could be extracted from it.',
    REASON_WORKER_ERROR: 'Indexing failed unexpectedly.',
    REASON_NEVER_INDEXED: 'Indexing never ran for this document — most often the upload landed while the Celery worker was down.',
}

# ElementTree's ParseError messages are precise about WHERE but say nothing
# about what to actually do, and the same handful recur across real exports.
# Matched on the message prefix ET itself produces (the ", line N, column M"
# suffix is kept in the detail text, so the author can go straight to the spot).
_XML_ERROR_HINTS = (
    ('not well-formed (invalid token)',
     'Usually a bare "&" or "<" in the text — they must be escaped as &amp; and &lt;.'),
    ('mismatched tag',
     'A tag is closed with a different name than it was opened with, or a </segment> is missing.'),
    ('unclosed token',
     'A tag or attribute value was opened and never closed — check for a missing ">" or quote.'),
    ('no element found',
     'The file ends before the document is closed, or it contains no XML at all — check it uploaded completely.'),
    ('junk after document element',
     'There is content after the closing </document> tag — an XML file may only have one root element.'),
    ('undefined entity',
     'An HTML entity such as &nbsp; was used; XML only knows &amp; &lt; &gt; &quot; &apos; (use a numeric entity like &#160; otherwise).'),
    ('unbound prefix',
     'A namespace prefix (the "ns:" part of a tag name) is used without being declared with xmlns:.'),
    ('duplicate attribute',
     'The same attribute is given twice on one element.'),
    ('syntax error',
     'The file starts with something other than XML — check it is the corpus export and not, say, a Word or HTML file.'),
)


def _xml_error_detail(error):
    message = str(error)
    for prefix, hint in _XML_ERROR_HINTS:
        if message.startswith(prefix):
            return f'XML parse error — {message}. {hint}'
    return f'XML parse error — {message}.'


def expects_structured_xml(content, title=''):
    """True when the upload is evidently MEANT to be corpus XML — by filename
    or by starting with a tag.

    The distinction matters because extract_words() falls back to flat
    tokenization for anything it can't parse, which is the right behavior for
    a genuine plain-text upload and the wrong one for a corpus export with a
    typo in it: the fallback happily turns `<segment id='4' ...>` into the
    "words" segment, id, 4 and reports a healthy token count. Without this
    check a malformed export is indistinguishable from a successful one.
    """
    if title.lower().endswith('.xml'):
        return True
    return content.lstrip().startswith('<')


def diagnose_content(content, title=''):
    """Returns (reason_code, detail) for content that cannot be indexed into a
    usable document, or (None, '') when it can.

    Structural checks only — "did this produce any tokens" is left to the
    caller, which has the real counts from build_tokens() and shouldn't
    tokenize twice to find out (worker/tasks/indexing.py).
    """
    if not content or not content.strip():
        return REASON_EMPTY_CONTENT, REASON_MESSAGES[REASON_EMPTY_CONTENT]

    if not expects_structured_xml(content, title):
        # Genuine plain text: the flat tokenizer is the correct reading of it,
        # not a fallback that hides a problem. Nothing structural to check.
        return None, ''

    try:
        root = ET.fromstring(content)
    except ET.ParseError as error:
        return REASON_XML_MALFORMED, _xml_error_detail(error)

    if root.find('body') is None:
        found = ', '.join(f'<{child.tag}>' for child in root) or 'nothing'
        return REASON_XML_NO_BODY, (
            f'{REASON_MESSAGES[REASON_XML_NO_BODY]} <{root.tag}> contains {found}.'
        )

    return None, ''
