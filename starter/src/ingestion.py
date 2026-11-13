"""Read and normalize source PDFs while keeping full page-level traceability."""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from .models import DocumentPage

_CONG_BAO_HEADER_RE = re.compile(
    r"^\s*(?:\d+\s+)?CÔNG BÁO/Số\s+\d+\s*\+\s*\d+/Ngày\s+\d{1,2}-\d{1,2}-\d{4}(?:\s+\d+)?\s*$"
)
_STRUCTURAL_START_RE = re.compile(
    r"^(?:"
    r"Chương\s+[IVXLC]+|"
    r"Mục\s+\d+|"
    r"Điều\s+\d+[a-zđ]?\.|"
    r"\d+\.\s|"
    r"[a-zđ]\)\s"
    r")"
)
_PARAGRAPH_BREAK_RE = re.compile(r"^(?:Chương\s+[IVXLC]+|Mục\s+\d+|Điều\s+\d+[a-zđ]?\.|\d+\.\s)")
_CHAPTER_LINE_RE = re.compile(r"^(Chương\s+[IVXLC]+)\b", re.MULTILINE)


def normalize_text(text: str) -> str:
    """Normalize Unicode to NFC, strip Cong Bao headers, and reconstruct legal paragraphs."""
    if not text:
        return ""

    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")

    cleaned_lines: list[str] = []
    for raw_line in text.split("\n"):
        line = re.sub(r"[ \t]+", " ", raw_line).strip()
        if not line:
            continue
        if _CONG_BAO_HEADER_RE.match(line):
            continue
        cleaned_lines.append(line)

    blocks: list[str] = []
    current_parts: list[str] = []

    for line in cleaned_lines:
        if _PARAGRAPH_BREAK_RE.match(line):
            if current_parts:
                blocks.append(" ".join(current_parts))
                current_parts = []
            current_parts.append(line)
        elif _STRUCTURAL_START_RE.match(line):
            # Keep sub-points (a), b), ...) on a new line within the same paragraph block.
            if current_parts:
                blocks.append(" ".join(current_parts))
                current_parts = []
            current_parts.append(line)
        else:
            current_parts.append(line)

    if current_parts:
        blocks.append(" ".join(current_parts))

    normalized = "\n\n".join(blocks)
    return re.sub(r"\n{3,}", "\n\n", normalized).strip()


def load_pdf(
    path: Path,
    document_id: str,
    source: str,
    allowed_pages: set[int] | None = None,
    base_metadata: dict[str, Any] | None = None,
    error_log: list[dict[str, Any]] | None = None,
) -> list[DocumentPage]:
    """Extract physical pages, keep page/source metadata, and log empty or failed pages."""
    reader = PdfReader(path)
    pages: list[DocumentPage] = []
    current_chapter = "PHAN_MO_DAU"

    for page_num, pdf_page in enumerate(reader.pages, start=1):
        try:
            raw_text = pdf_page.extract_text() or ""
        except Exception as exc:  # pragma: no cover - defensive logging for corrupted pages
            if error_log is not None:
                error_log.append(
                    {
                        "document_id": document_id,
                        "source": source,
                        "page": page_num,
                        "issue": "extract_error",
                        "detail": str(exc),
                    }
                )
            continue

        norm_text = normalize_text(raw_text)
        chapter_matches = _CHAPTER_LINE_RE.findall(norm_text)
        if chapter_matches:
            current_chapter = chapter_matches[-1]

        if allowed_pages is not None and page_num not in allowed_pages:
            continue

        if len(norm_text) < 50:
            if error_log is not None:
                error_log.append(
                    {
                        "document_id": document_id,
                        "source": source,
                        "page": page_num,
                        "issue": "empty_or_short_page",
                        "chars": len(norm_text),
                    }
                )
            continue

        meta: dict[str, Any] = dict(base_metadata or {})
        meta["chapter"] = current_chapter
        meta["char_count"] = len(norm_text)

        pages.append(
            DocumentPage(
                document_id=document_id,
                source=source,
                page=page_num,
                text=norm_text,
                metadata=meta,
            )
        )

    return pages
