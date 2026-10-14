"""Text from PDF attachments, page by page, using the PDF's text layer.

Scanned pages have no text layer and come back empty; reading those with OCR
is a later step.
"""

from __future__ import annotations

import io
import logging

from pypdf import PdfReader
from pypdf.errors import PdfReadError

MAX_PAGES = 500  # past this, a packet is read only up to here

logging.getLogger("pypdf").setLevel(logging.ERROR)


def pdf_pages(data: bytes, max_pages: int = MAX_PAGES) -> list[str]:
    """One string per page; [] if the file isn't a readable PDF."""
    if not data.startswith(b"%PDF"):
        return []
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = reader.pages[:max_pages]
    except (PdfReadError, ValueError, OSError):
        return []
    texts = []
    for page in pages:
        try:
            texts.append(page.extract_text() or "")
        except Exception:  # one bad page shouldn't lose the rest of the packet
            texts.append("")
    return texts
