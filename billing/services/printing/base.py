"""
Printing abstraction.

Why an abstraction at all?
-------------------------
"Click print and paper comes out, with no dialog" is only possible when the
process doing the printing runs on the machine the printer is attached to.
That is exactly the case here: Django runs on the shop PC itself, so Python
can hand the PDF straight to the OS spooler.

Each concrete backend below is one way of doing that. The view layer only ever
talks to `get_backend()`, so switching from the mock to a real printer is a
one-line change in .env:

    PRINT_BACKEND=dummy     # no printer attached (development / this machine)
    PRINT_BACKEND=windows   # silent printing through the Windows spooler
    PRINT_BACKEND=cups      # Linux / macOS `lp`
    PRINT_BACKEND=escpos    # thermal receipt printer over network or USB
    PRINT_BACKEND=browser   # last resort: let the browser print dialog do it
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


class PrinterError(RuntimeError):
    """Raised when a backend cannot deliver the document to a printer."""


@dataclass
class PrintResult:
    """Outcome of one attempt to print one document."""

    success: bool
    backend: str
    message: str = ""
    printer_name: str = ""
    copies: int = 1
    spooled_file: str = ""
    mocked: bool = False
    # Set by the `browser` backend so the view knows to open the print dialog.
    needs_browser_dialog: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def db_status(self) -> str:
        from billing.models import PrintJob

        if not self.success:
            return PrintJob.Status.FAILED
        return PrintJob.Status.MOCKED if self.mocked else PrintJob.Status.SENT


class BasePrintBackend:
    """
    Interface every backend implements.

    `print_pdf` receives the finished PDF as bytes plus a filename hint, and
    must return a PrintResult instead of raising for expected failures (a
    missing printer is an everyday event in a shop, not a crash).
    """

    name: str = "base"

    def __init__(self, printer_name: str | None = None, copies: int = 1, **options):
        self.printer_name = printer_name
        self.copies = max(1, int(copies or 1))
        self.options = options

    # -- to implement ------------------------------------------------------
    def print_pdf(self, pdf_bytes: bytes, filename: str, **context) -> PrintResult:
        raise NotImplementedError

    def list_printers(self) -> list[str]:
        """Printer names available to this backend (best effort)."""
        return []

    def is_available(self) -> bool:
        """False when the backend's dependencies are missing on this machine."""
        return True

    # -- helpers -----------------------------------------------------------
    def _ok(self, message: str, **kw) -> PrintResult:
        logger.info("[print:%s] %s", self.name, message)
        return PrintResult(
            success=True,
            backend=self.name,
            message=message,
            printer_name=self.printer_name or "",
            copies=self.copies,
            **kw,
        )

    def _fail(self, message: str, **kw) -> PrintResult:
        logger.error("[print:%s] %s", self.name, message)
        return PrintResult(
            success=False,
            backend=self.name,
            message=message,
            printer_name=self.printer_name or "",
            copies=self.copies,
            **kw,
        )

    def __repr__(self):
        return f"<{type(self).__name__} printer={self.printer_name!r} copies={self.copies}>"
