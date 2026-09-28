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


def _walk_body(body, original_words, corrected_words):
    def add_text(text):
        words = _tokenize(text)
        original_words.extend(words)
        corrected_words.extend(words)

    add_text(body.text)

    for elem in body:
        if elem.tag == 'segment':
            original_words.extend(_tokenize(elem.text))
            correction = elem.get('Correction', '')
            corrected_words.extend(_tokenize(correction))
        else:
            add_text(''.join(elem.itertext()))

        add_text(elem.tail)


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
    streams, and each <segment> contributes its inner text to the original stream and
    its Correction attribute (if non-empty) to the corrected stream. Falls back to flat
    word tokenization of the raw content (identical for both streams) when the content
    isn't parseable XML with a <body> element.
    """
    original_words, corrected_words, _ = parse_document(content)

    return original_words, corrected_words
