"""PDF tools - read, extract text and tables from PDF files."""

import subprocess
import shutil
import tempfile
import os
import re
from pathlib import Path
from typing import Optional


def _has_pdftotext() -> bool:
    """Check if pdftotext (poppler) is available."""
    return shutil.which("pdftotext") is not None


def _has_python_pdf_libs() -> bool:
    """Check if any Python PDF library is available."""
    try:
        import PyPDF2  # noqa
        return True
    except ImportError:
        pass
    try:
        import pdfplumber  # noqa
        return True
    except ImportError:
        pass
    try:
        import pdfminer  # noqa
        return True
    except ImportError:
        pass
    try:
        import fitz  # PyMuPDF  # noqa
        return True
    except ImportError:
        pass
    return False


def _extract_with_pdftotext(pdf_path: str) -> str:
    """Extract text using pdftotext (poppler-utils)."""
    result = subprocess.run(
        ["pdftotext", pdf_path, "-"],
        capture_output=True,
        text=True,
        timeout=60,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout.strip()


def _extract_with_pypdf2(pdf_path: str) -> str:
    """Extract text using PyPDF2."""
    import PyPDF2
    text_parts = []
    with open(pdf_path, "rb") as f:
        reader = PyPDF2.PdfReader(f)
        for i, page in enumerate(reader.pages):
            t = page.extract_text()
            if t.strip():
                text_parts.append(f"--- Page {i+1} ---\n{t}")
    return "\n\n".join(text_parts)


def _extract_with_pdfplumber(pdf_path: str) -> str:
    """Extract text using pdfplumber (best for tables)."""
    import pdfplumber
    text_parts = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            text = page.extract_text()
            if text and text.strip():
                text_parts.append(f"--- Page {i+1} ---\n{text.strip()}")
            # Try to extract tables
            tables = page.extract_tables()
            if tables:
                for ti, table in enumerate(tables):
                    table_str = _format_table(table)
                    if table_str:
                        text_parts.append(f"--- Table {ti+1} (Page {i+1}) ---\n{table_str}")
    return "\n\n".join(text_parts)


def _extract_with_pymupdf(pdf_path: str) -> str:
    """Extract text using PyMuPDF (fitz)."""
    import fitz
    text_parts = []
    doc = fitz.open(pdf_path)
    for i, page in enumerate(doc):
        text = page.get_text()
        if text.strip():
            text_parts.append(f"--- Page {i+1} ---\n{text.strip()}")
    doc.close()
    return "\n\n".join(text_parts)


def _extract_with_pdfminer(pdf_path: str) -> str:
    """Extract text using pdfminer.six."""
    from pdfminer.high_level import extract_text
    return extract_text(pdf_path)


def _format_table(table: list[list[str | None]]) -> str:
    """Format extracted table as markdown-like text."""
    if not table or not table[0]:
        return ""
    # Clean None values
    clean = []
    for row in table:
        clean.append([cell.strip() if cell else "" for cell in row])
    # Calculate column widths
    col_widths = []
    for col_idx in range(len(clean[0])):
        widths = [len(row[col_idx]) for row in clean if col_idx < len(row)]
        col_widths.append(max(widths) if widths else 10)
    # Format as pipe-separated
    lines = []
    for ri, row in enumerate(clean):
        cells = []
        for ci, cell in enumerate(row):
            if ci < len(col_widths):
                cells.append(cell.ljust(col_widths[ci]))
        lines.append(" | ".join(cells))
        if ri == 0:
            lines.append("-+-".join("-" * w for w in col_widths))
    return "\n".join(lines)


def read_pdf(pdf_path: str, pages: Optional[str] = None) -> str:
    """Read and extract text from a PDF file.

    Args:
        pdf_path: Path to the PDF file
        pages: Optional page range, e.g. "1-3,5" (1-indexed)

    Returns:
        Extracted text content
    """
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")
    if not path.is_file():
        raise IsADirectoryError(f"Path is not a file: {pdf_path}")

    # Try extraction methods in order of preference
    text = ""
    if _has_pdftotext():
        text = _extract_with_pdftotext(str(path))
    elif _has_python_pdf_libs():
        try:
            import pdfplumber
            text = _extract_with_pdfplumber(str(path))
        except ImportError:
            try:
                import fitz
                text = _extract_with_pymupdf(str(path))
            except ImportError:
                try:
                    import PyPDF2
                    text = _extract_with_pypdf2(str(path))
                except ImportError:
                    try:
                        import pdfminer
                        text = _extract_with_pdfminer(str(path))
                    except ImportError:
                        pass
    else:
        # Try to use macOS built-in tools
        try:
            result = subprocess.run(
                ["textutil", "-convert", "txt", "-stdout", str(path)],
                capture_output=True,
                text=True,
                timeout=60,
                encoding="utf-8",
                errors="replace",
            )
            if result.returncode == 0:
                text = result.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    if not text:
        # Last resort: try to extract any text with strings command
        try:
            result = subprocess.run(
                ["strings", str(path)],
                capture_output=True,
                text=True,
                timeout=30,
                encoding="utf-8",
                errors="replace",
            )
            text = result.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    if not text:
        return "No text could be extracted from the PDF. The PDF may be scanned/image-based."

    # Filter pages if requested
    if pages:
        filtered_parts = []
        page_blocks = re.split(r'^--- Page (\d+) ---', text, flags=re.MULTILINE)
        # Parse page ranges like "1-3,5"
        requested_pages = set()
        for part in pages.split(","):
            part = part.strip()
            if "-" in part:
                start, end = part.split("-", 1)
                requested_pages.update(range(int(start.strip()), int(end.strip()) + 1))
            else:
                requested_pages.add(int(part))

        # Reconstruct filtered text
        current_page = None
        for block in page_blocks:
            if block.isdigit():
                current_page = int(block)
            elif current_page and current_page in requested_pages:
                filtered_parts.append(f"--- Page {current_page} ---\n{block.strip()}")
            elif current_page is None and block.strip():
                filtered_parts.append(block.strip())

        if filtered_parts:
            text = "\n\n".join(filtered_parts)

    # Truncate very long text
    MAX_LENGTH = 50000
    if len(text) > MAX_LENGTH:
        text = text[:MAX_LENGTH] + f"\n\n... [truncated, {len(text) - MAX_LENGTH} more chars]"

    return text


def pdf_info(pdf_path: str) -> str:
    """Get metadata and info about a PDF file."""
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")

    info_lines = [
        f"Filename: {path.name}",
        f"Size: {path.stat().st_size:,} bytes",
    ]

    # Try to get page count and metadata
    try:
        import PyPDF2
        with open(path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            info_lines.append(f"Pages: {len(reader.pages)}")
            meta = reader.metadata
            if meta:
                for key, val in meta.items():
                    if val:
                        info_lines.append(f"{key}: {val}")
        return "\n".join(info_lines)
    except ImportError:
        pass

    # Fallback: try pdftotext or macOS
    try:
        result = subprocess.run(
            ["pdfinfo", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return "\n".join(info_lines) + "\n(Install PyPDF2 for detailed metadata: pip install PyPDF2)"
