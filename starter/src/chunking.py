"""Implement fixed-size and structure-aware (paragraph/article) chunking."""
from __future__ import annotations

import re

from .models import Chunk, DocumentPage

_ARTICLE_RE = re.compile(r"\b(Điều\s+\d+[a-zđ]?)\b")


def _make_chunk(
    page: DocumentPage,
    strategy_tag: str,
    idx: int,
    text: str,
) -> Chunk:
    meta = dict(page.metadata)
    articles = _ARTICLE_RE.findall(text)
    if articles:
        meta["article"] = articles[0]
    meta["chunk_chars"] = len(text)
    return Chunk(
        chunk_id=f"{strategy_tag}:{page.document_id}:p{page.page:02d}:c{idx:03d}",
        document_id=page.document_id,
        source=page.source,
        page=page.page,
        text=text,
        metadata=meta,
    )


def fixed_size(page: DocumentPage, size: int = 500, overlap: int = 50) -> list[Chunk]:
    """Split normalized page text into overlapping fixed-size character windows."""
    if size <= 0:
        raise ValueError("size must be positive")
    if overlap < 0 or overlap >= size:
        raise ValueError("overlap must satisfy 0 <= overlap < size")

    text = page.text.strip()
    if not text:
        return []

    chunks: list[Chunk] = []
    step = size - overlap
    start = 0
    idx = 1
    n = len(text)

    while start < n:
        end = min(start + size, n)
        piece = text[start:end].strip()
        if piece:
            chunks.append(_make_chunk(page, f"fixed_{size}_{overlap}", idx, piece))
            idx += 1
        if end >= n:
            break
        start += step

    return chunks


def _split_oversized_paragraph(paragraph: str, max_size: int) -> list[str]:
    """Split a single paragraph exceeding max_size on sentence or word boundaries."""
    if len(paragraph) <= max_size:
        return [paragraph]

    sentences = re.split(r"(?<=[.;:])\s+", paragraph)
    pieces: list[str] = []
    buf = ""

    for sent in sentences:
        if not sent:
            continue
        if len(sent) > max_size:
            if buf:
                pieces.append(buf)
                buf = ""
            # Fallback hard-split by words if a single sentence exceeds max_size.
            words = sent.split(" ")
            wbuf = ""
            for w in words:
                candidate = f"{wbuf} {w}".strip() if wbuf else w
                if len(candidate) <= max_size:
                    wbuf = candidate
                else:
                    if wbuf:
                        pieces.append(wbuf)
                    wbuf = w[:max_size]
            if wbuf:
                pieces.append(wbuf)
            continue

        candidate = f"{buf} {sent}".strip() if buf else sent
        if len(candidate) <= max_size:
            buf = candidate
        else:
            if buf:
                pieces.append(buf)
            buf = sent

    if buf:
        pieces.append(buf)
    return pieces


def by_paragraph(page: DocumentPage, max_size: int = 1000) -> list[Chunk]:
    """Group paragraphs/clauses cleanly up to max_size, keeping article context."""
    if max_size <= 0:
        raise ValueError("max_size must be positive")

    raw_paragraphs = [p.strip() for p in page.text.split("\n\n") if p.strip()]
    if not raw_paragraphs:
        return []

    units: list[str] = []
    for para in raw_paragraphs:
        units.extend(_split_oversized_paragraph(para, max_size))

    chunks: list[Chunk] = []
    buf = ""
    idx = 1
    is_article_heading = re.compile(r"^(?:Chương\s+[IVXLC]+|Mục\s+\d+|Điều\s+\d+[a-zđ]?\.)$")

    for unit in units:
        # Flush before a new Điều heading when the buffer already has substantive text.
        if re.match(r"^Điều\s+\d+[a-zđ]?\.", unit) and buf and len(buf) >= max_size // 3:
            chunks.append(_make_chunk(page, f"para_{max_size}", idx, buf))
            idx += 1
            buf = unit
            continue

        candidate = f"{buf}\n\n{unit}" if buf else unit
        if len(candidate) <= max_size or (buf and is_article_heading.match(buf)):
            buf = candidate
        else:
            chunks.append(_make_chunk(page, f"para_{max_size}", idx, buf))
            idx += 1
            buf = unit

    if buf:
        chunks.append(_make_chunk(page, f"para_{max_size}", idx, buf))

    return chunks
