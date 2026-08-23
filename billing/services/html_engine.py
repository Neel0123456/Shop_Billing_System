"""
Fallback engine: Django HTML template -> PDF via xhtml2pdf.

The Word template is the real bill format. This engine exists so that the app
is never dead in the water: it powers the on-screen preview, and it takes over
automatically if the Word template is missing or no converter is installed.

xhtml2pdf has limited CSS support (no flexbox / grid), so the print template
sticks to tables and absolute widths.
"""
from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.template.loader import render_to_string

from .context import build_invoice_context

logger = logging.getLogger(__name__)

PRINT_TEMPLATE = "billing/invoice_print.html"


class PdfGenerationError(RuntimeError):
    pass


def link_callback(uri: str, rel) -> str:
    """
    Map /static/ and /media/ URLs to filesystem paths.

    xhtml2pdf cannot fetch over HTTP, so assets must be handed over as paths.
    """
    if uri.startswith(("http://", "https://", "data:")):
        return uri

    static_url, media_url = settings.STATIC_URL, settings.MEDIA_URL

    if uri.startswith(media_url):
        path = Path(settings.MEDIA_ROOT) / uri.replace(media_url, "", 1)
    elif uri.startswith(static_url):
        rel_path = uri.replace(static_url, "", 1)
        path = Path(settings.STATIC_ROOT) / rel_path
        if not path.exists():
            for directory in settings.STATICFILES_DIRS:
                candidate = Path(directory) / rel_path
                if candidate.exists():
                    path = candidate
                    break
    else:
        return uri

    if not path.exists():
        logger.warning("PDF asset not found, skipping: %s", uri)
        return uri
    return str(path)


def render_html(invoice, *, printed_by=None, for_screen: bool = False) -> str:
    ctx = build_invoice_context(invoice, printed_by=printed_by, for_html=True)
    ctx["for_screen"] = for_screen
    return render_to_string(PRINT_TEMPLATE, ctx)


def render_pdf(invoice, *, printed_by=None) -> bytes:
    try:
        from xhtml2pdf import pisa
    except ImportError as exc:  # pragma: no cover
        raise PdfGenerationError(
            "xhtml2pdf is not installed. Run: pip install -r requirements.txt"
        ) from exc

    html = render_html(invoice, printed_by=printed_by)
    buffer = BytesIO()
    status = pisa.CreatePDF(
        src=html, dest=buffer, encoding="utf-8", link_callback=link_callback
    )
    if status.err:
        raise PdfGenerationError(
            f"xhtml2pdf reported {status.err} error(s) for {invoice.number}"
        )

    pdf_bytes = buffer.getvalue()
    if not pdf_bytes:
        raise PdfGenerationError(f"Empty PDF produced for {invoice.number}")

    logger.info("HTML engine rendered %s (%.1f KB)", invoice.number, len(pdf_bytes) / 1024)
    return pdf_bytes
