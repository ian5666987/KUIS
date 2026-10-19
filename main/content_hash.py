"""The one formula for Document.content_hash (docs/data-processing.md).

Its own module, depending on nothing but main/corpus_parsing.py, so that
main/models.py can import it: Document.save() recomputes content_hash on every
write of `content`, and main/document_ingest.py — which every upload goes
through — imports the models, so the formula can't live there without an
import cycle. main/document_ingest.py re-exports both functions, which is where
every other caller has always imported them from.
"""

import hashlib
import re
import xml.etree.ElementTree as ET

from .corpus_parsing import looks_like_structured_xml

_CRLF_RE = re.compile(r"\r\n?")


def canonicalize_for_hash(content):
    """Normalizes representationally-irrelevant differences before hashing, so
    two uploads of "the same" document dedup even when their raw bytes differ
    (BOM presence, CRLF vs LF, leading/trailing whitespace, XML attribute
    order) — see docs/data-processing.md: these are the ways two exports of
    the same document routinely vary. Deliberately narrow: internal
    whitespace, case, and everything else about the text is left untouched,
    so meaningfully different plain text never collides. Never touches what
    gets *stored* — Document.content always keeps the exact raw text as
    uploaded; only the hash input is canonicalized.
    """
    if content.startswith("\ufeff"):
        content = content[1:]
    content = _CRLF_RE.sub("\n", content)
    content = content.strip()

    if looks_like_structured_xml(content):
        try:
            return ET.canonicalize(content, strip_text=False)
        except Exception:
            # Parses well enough to look like corpus XML (has a <body>) but
            # not strictly C14N-canonicalizable (e.g. an unbound namespace
            # prefix) — fall back to the line/BOM-normalized text instead of
            # failing the upload. Safe failure mode: a missed dedup
            # opportunity, never a false positive or a crash.
            return content

    return content


def compute_content_hash(content):
    """The one formula for Document.content_hash — used at upload time
    (main/document_ingest.py), on every save that writes `content`
    (Document.save()) and by worker/tasks/indexing.py's post-index recompute,
    so all three always agree."""
    return hashlib.sha256(canonicalize_for_hash(content).encode("utf-8")).hexdigest()
