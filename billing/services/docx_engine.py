"""
Word-template engine:  static .docx  ->  filled .docx  ->  PDF.

The shop owner designs the bill in Word exactly as he wants it printed, and
drops docxtpl (Jinja2) placeholders where the data belongs::

    Bill No: {{ invoice_number }}          Date: {{ invoice_date }}
    Name   : {{ customer_name }}
    Phone  : {{ customer_phone }}
    City   : {{ customer_city }}

Line items go in a one-data-row table, with the row itself wrapped in the
special ``{%tr ... %}`` tags so the *row* repeats rather than the text::

    | {%tr for item in items %}{{ item.sr }} | {{ item.description }} | ...
    | {%tr endfor %}

Anything conditional uses ``{%p if ... %}`` so the whole paragraph disappears
when unused - that is how a zero discount, an empty email or a missing due
date leave no blank line behind.
"""
from __future__ import annotations

import logging
from pathlib import Path

from django.conf import settings

from .context import build_invoice_context
from .docx_convert import DocxConversionError, convert_docx_to_pdf

logger = logging.getLogger(__name__)


class TemplateMissingError(RuntimeError):
    """The configured Word template does not exist."""


def template_path() -> Path:
    return Path(settings.DOCX_TEMPLATE_PATH)


def template_exists() -> bool:
    return template_path().is_file()


def render_docx(invoice, output_path: Path, *, printed_by=None) -> Path:
    """Fill the Word template for one invoice and write the .docx."""
    try:
        from docxtpl import DocxTemplate
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "docxtpl is not installed. Run: pip install -r requirements.txt"
        ) from exc

    tpl_path = template_path()
    if not tpl_path.is_file():
        raise TemplateMissingError(
            f"Word template not found at {tpl_path}. Either place your .docx "
            f"there, point DOCX_TEMPLATE_PATH at it in .env, or generate a "
            f"starter with:  python manage.py docx_template --create"
        )

    context = build_invoice_context(invoice, printed_by=printed_by)

    doc = DocxTemplate(str(tpl_path))
    doc.render(context, autoescape=True)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_path))

    logger.info("Filled Word template for %s -> %s", invoice.number, output_path)
    return output_path


def render_pdf(invoice, *, printed_by=None, keep_docx: bool | None = None) -> bytes:
    """
    Full pipeline for one bill: fill the template, convert, return PDF bytes.

    The intermediate .docx is kept by default (DOCX_KEEP_INTERMEDIATE) because
    it is the editable copy the owner may want when a customer asks for a
    change to an already-issued bill.
    """
    from .documents import archive_subfolder

    keep = settings.DOCX_KEEP_INTERMEDIATE if keep_docx is None else keep_docx
    stem = Path(invoice.pdf_filename).stem

    # Same dated layout as the PDF archive, so the two folders mirror each other.
    docx_dir = Path(settings.INVOICE_DOCX_DIR)
    subfolder = archive_subfolder(invoice)
    if subfolder:
        docx_dir = docx_dir / subfolder

    docx_path = docx_dir / f"{stem}.docx"
    pdf_path = docx_dir / f"{stem}.pdf"

    render_docx(invoice, docx_path, printed_by=printed_by)
    try:
        convert_docx_to_pdf(docx_path, pdf_path)
        pdf_bytes = pdf_path.read_bytes()
    except DocxConversionError:
        raise
    finally:
        if not keep:
            docx_path.unlink(missing_ok=True)

    # The PDF next to the .docx is a working file; documents.save_pdf() writes
    # the archived copy to INVOICE_PDF_DIR.
    pdf_path.unlink(missing_ok=True)
    return pdf_bytes
