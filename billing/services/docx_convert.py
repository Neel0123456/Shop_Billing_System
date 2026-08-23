"""
DOCX -> PDF conversion.

Two converters, both of which run locally on the shop PC:

``word``
    Drives an installed Microsoft Word through COM automation (pywin32).
    Highest fidelity: whatever the template looks like in Word is exactly what
    comes out of the printer. Word is started invisibly and reused across
    conversions within one call.

``libreoffice``
    ``soffice --headless --convert-to pdf``. Needed only if Word is absent.

``auto`` (default) tries Word first, then LibreOffice.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)


class DocxConversionError(RuntimeError):
    """Raised when a .docx could not be turned into a PDF."""


# ---------------------------------------------------------------------------
# Microsoft Word (COM)
# ---------------------------------------------------------------------------
WD_FORMAT_PDF = 17  # WdSaveFormat.wdFormatPDF


def word_available() -> bool:
    """True when Word can be driven through COM on this machine."""
    if os.name != "nt":
        return False
    try:
        import pythoncom  # type: ignore  # noqa: F401
        import win32com.client  # type: ignore
    except ImportError:
        return False
    try:
        import winreg

        winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "Word.Application")
        return True
    except OSError:
        return False


def convert_with_word(docx_path: Path, pdf_path: Path) -> Path:
    """
    Convert via Word COM.

    Notes for the Windows service case: COM must be initialised on the calling
    thread, which is why pythoncom.CoInitialize() is called explicitly - Django
    handles each request on a worker thread, not the main one.
    """
    try:
        import pythoncom  # type: ignore
        import win32com.client  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise DocxConversionError(
            "pywin32 is required for Word conversion. pip install pywin32"
        ) from exc

    docx_path, pdf_path = Path(docx_path).resolve(), Path(pdf_path).resolve()
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    pythoncom.CoInitialize()
    word = None
    doc = None
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0  # wdAlertsNone: never block on a modal dialog

        doc = word.Documents.Open(
            str(docx_path), ReadOnly=True, AddToRecentFiles=False, Visible=False
        )
        # Refresh any TOC / field codes before exporting.
        try:
            doc.Fields.Update()
        except Exception:
            pass

        doc.SaveAs(str(pdf_path), FileFormat=WD_FORMAT_PDF)
        logger.info("Word converted %s -> %s", docx_path.name, pdf_path.name)
        return pdf_path
    except Exception as exc:
        raise DocxConversionError(f"Word could not convert {docx_path.name}: {exc}") from exc
    finally:
        try:
            if doc is not None:
                doc.Close(SaveChanges=False)
        except Exception:
            pass
        try:
            if word is not None:
                word.Quit()
        except Exception:
            pass
        pythoncom.CoUninitialize()


# ---------------------------------------------------------------------------
# LibreOffice
# ---------------------------------------------------------------------------
LIBREOFFICE_CANDIDATES = [
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    "/usr/bin/soffice",
    "/usr/bin/libreoffice",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
]


def find_libreoffice() -> str | None:
    configured = settings.LIBREOFFICE_PATH
    if configured and os.path.exists(configured):
        return configured
    found = shutil.which("soffice") or shutil.which("libreoffice")
    if found:
        return found
    return next((p for p in LIBREOFFICE_CANDIDATES if os.path.exists(p)), None)


def convert_with_libreoffice(docx_path: Path, pdf_path: Path) -> Path:
    soffice = find_libreoffice()
    if not soffice:
        raise DocxConversionError(
            "LibreOffice was not found. Install it or set LIBREOFFICE_PATH in .env."
        )

    docx_path, pdf_path = Path(docx_path).resolve(), Path(pdf_path).resolve()
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    # soffice always names the output <stem>.pdf, so convert into a temp dir
    # and then move it to the requested filename.
    with tempfile.TemporaryDirectory() as tmpdir:
        proc = subprocess.run(
            [
                soffice,
                "--headless",
                "--norestore",
                "--convert-to",
                "pdf:writer_pdf_Export",
                "--outdir",
                tmpdir,
                str(docx_path),
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
        produced = Path(tmpdir) / f"{docx_path.stem}.pdf"
        if proc.returncode != 0 or not produced.exists():
            raise DocxConversionError(
                f"LibreOffice failed ({proc.returncode}): "
                f"{proc.stderr.strip() or proc.stdout.strip()}"
            )
        shutil.move(str(produced), str(pdf_path))

    logger.info("LibreOffice converted %s -> %s", docx_path.name, pdf_path.name)
    return pdf_path


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------
def convert_docx_to_pdf(docx_path: Path, pdf_path: Path, converter: str | None = None) -> Path:
    """Convert using the configured converter, with a clear error if none works."""
    converter = (converter or settings.DOCX_TO_PDF_CONVERTER or "auto").lower()

    if converter == "word":
        return convert_with_word(docx_path, pdf_path)
    if converter == "libreoffice":
        return convert_with_libreoffice(docx_path, pdf_path)

    # auto
    errors = []
    if word_available():
        try:
            return convert_with_word(docx_path, pdf_path)
        except DocxConversionError as exc:
            errors.append(str(exc))
            logger.warning("Word conversion failed, trying LibreOffice: %s", exc)
    if find_libreoffice():
        try:
            return convert_with_libreoffice(docx_path, pdf_path)
        except DocxConversionError as exc:
            errors.append(str(exc))

    raise DocxConversionError(
        "No DOCX to PDF converter is usable on this machine. Install Microsoft "
        "Word (recommended) or LibreOffice, or set DOCUMENT_ENGINE=html to use "
        "the built-in HTML renderer. Details: " + " | ".join(errors)
    )


def converter_status() -> dict[str, object]:
    """Diagnostics for the print-settings page."""
    soffice = find_libreoffice()
    return {
        "configured": settings.DOCX_TO_PDF_CONVERTER,
        "word_available": word_available(),
        "libreoffice_path": soffice,
        "libreoffice_available": bool(soffice),
    }
