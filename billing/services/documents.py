"""
Public document API. Views and management commands only talk to this module.

    build_pdf(invoice)         -> bytes        (Word template -> PDF)
    archive_pdf(invoice)       -> Path         (saved into INVOICE_PDF_DIR)
    print_invoice(invoice)     -> PrintJob     (build, archive, send to printer)

Engine selection (DOCUMENT_ENGINE in .env):

    docx  the shop owner's Word template, converted by Word or LibreOffice.
          This is the real bill format.
    html  the built-in HTML template rendered by xhtml2pdf. Used as an
          automatic fallback so billing never stops when Word is unavailable.
"""
from __future__ import annotations

import logging
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from . import docx_engine, html_engine
from .docx_convert import DocxConversionError
from .printing import get_backend

logger = logging.getLogger(__name__)


class DocumentError(RuntimeError):
    """Any failure while producing the bill document."""


def active_engine() -> str:
    """Which engine will actually be used, after availability checks."""
    configured = (settings.DOCUMENT_ENGINE or "docx").lower()
    if configured == "docx" and not docx_engine.template_exists():
        return "html"
    return configured


def build_pdf(invoice, *, printed_by=None, engine: str | None = None) -> bytes:
    """
    Produce the PDF bytes for one invoice.

    Falls back from docx to html rather than failing the sale: a shop must
    still be able to hand over a bill when Word misbehaves. The fallback is
    logged loudly so the real problem is not hidden.
    """
    engine = (engine or active_engine()).lower()

    if engine == "html":
        return html_engine.render_pdf(invoice, printed_by=printed_by)

    try:
        return docx_engine.render_pdf(invoice, printed_by=printed_by)
    except (docx_engine.TemplateMissingError, DocxConversionError) as exc:
        logger.error(
            "Word pipeline unavailable for %s (%s). Falling back to the HTML "
            "engine so the bill can still be printed.",
            invoice.number,
            exc,
        )
        return html_engine.render_pdf(invoice, printed_by=printed_by)


def archive_subfolder(invoice) -> str:
    """
    The dated subfolder a bill is filed under.

    `date` (the default) creates one folder per day, named `YYYY-MM-DD`, so a
    day's bills sit together and are trivial to find or back up.

    The folder comes from the *bill's own date*, not from "now". They are the
    same thing for normal same-day billing, but it means regenerating an old
    bill's PDF refiles it where it already belongs instead of dropping a second
    copy into today's folder.
    """
    from billing.models import InvoiceSequence

    mode = (settings.INVOICE_ARCHIVE_SUBFOLDER or "date").lower()
    if mode == "date":
        return invoice.invoice_date.strftime("%Y-%m-%d")
    if mode == "month":
        return invoice.invoice_date.strftime("%Y-%m")
    if mode == "year":
        return InvoiceSequence.financial_year(invoice.invoice_date)
    return ""


def archive_dir(invoice) -> Path:
    """
    Folder the finished PDF is filed into, created on demand.

    INVOICE_PDF_DIR is fully configurable in .env, so bills can be archived
    straight into a synced or backed-up folder, e.g. D:/Shop/Bills.
    """
    base = Path(settings.INVOICE_PDF_DIR)
    subfolder = archive_subfolder(invoice)
    return base / subfolder if subfolder else base


def archive_pdf(invoice, *, printed_by=None, pdf_bytes: bytes | None = None) -> Path:
    """
    Write the PDF into today's folder and record its full path on the invoice.

    Storing the path means the bill can be reopened straight from the database
    row, without the caller having to know how the archive is laid out.
    """
    pdf_bytes = pdf_bytes if pdf_bytes is not None else build_pdf(invoice, printed_by=printed_by)

    directory = archive_dir(invoice)
    directory.mkdir(parents=True, exist_ok=True)  # auto-creates the date folder
    path = (directory / invoice.pdf_filename).resolve()
    path.write_bytes(pdf_bytes)

    if invoice.pk:
        invoice.pdf_path = str(path)
        invoice.pdf_saved_at = timezone.now()
        invoice.save(update_fields=["pdf_path", "pdf_saved_at", "updated_at"])

    logger.info("Archived %s to %s", invoice.number, path)
    return path


def print_invoice(invoice, *, requested_by=None, copies: int | None = None, archive: bool = True):
    """
    The "Save and Print" action: build -> archive -> hand to the printer.

    Always returns a saved PrintJob row, successful or not, so every print
    attempt is auditable from the invoice detail page.
    """
    from billing.models import PrintJob

    engine = active_engine()
    try:
        pdf_bytes = build_pdf(invoice, printed_by=requested_by)
    except Exception as exc:
        logger.exception("Could not build the PDF for %s", invoice.number)
        return PrintJob.objects.create(
            invoice=invoice,
            backend=settings.PRINT_BACKEND,
            status=PrintJob.Status.FAILED,
            message=f"PDF generation failed ({engine} engine): {exc}",
            requested_by=requested_by,
        )

    archived_path = ""
    if archive:
        try:
            archived_path = str(archive_pdf(invoice, pdf_bytes=pdf_bytes))
        except OSError as exc:
            logger.error("Could not archive %s: %s", invoice.number, exc)

    backend = get_backend(copies=copies or settings.PRINT_COPIES)
    result = backend.print_pdf(pdf_bytes, invoice.pdf_filename, invoice=invoice)

    job = PrintJob.objects.create(
        invoice=invoice,
        backend=result.backend,
        printer_name=result.printer_name,
        copies=result.copies,
        status=result.db_status,
        message=result.message,
        spooled_file=result.spooled_file or archived_path,
        requested_by=requested_by,
    )
    job.needs_browser_dialog = result.needs_browser_dialog  # transient hint for the view

    if result.success:
        invoice.print_count = invoice.print_count + 1
        invoice.last_printed_at = timezone.now()
        invoice.save(update_fields=["print_count", "last_printed_at"])

    return job


def pipeline_status() -> dict:
    """Everything the print-settings page needs to show. Read-only."""
    from .docx_convert import converter_status
    from .printing import available_backends

    return {
        "document_engine_configured": settings.DOCUMENT_ENGINE,
        "document_engine_active": active_engine(),
        "docx_template_path": str(docx_engine.template_path()),
        "docx_template_exists": docx_engine.template_exists(),
        "converter": converter_status(),
        "print_backend": settings.PRINT_BACKEND,
        "printer_name": settings.PRINTER_NAME or "(system default)",
        "print_copies": settings.PRINT_COPIES,
        "backends": available_backends(),
        "pdf_dir": str(settings.INVOICE_PDF_DIR),
        "docx_dir": str(settings.INVOICE_DOCX_DIR),
        "spool_dir": str(settings.PRINT_SPOOL_DIR),
        "printers": get_backend().list_printers(),
    }
