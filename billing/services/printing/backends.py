"""
Concrete print backends.

All of them take the same input (PDF bytes) and return the same PrintResult,
so the view code never changes when a real printer arrives.
"""
from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from .base import BasePrintBackend, PrintResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Dummy / mock  --  the default while no printer exists
# ---------------------------------------------------------------------------
class DummyPrintBackend(BasePrintBackend):
    """
    Pretends to print, and leaves proof behind.

    The exact bytes that *would* have gone to the printer are written to
    media/print_spool/, so the whole flow (form -> PDF -> print -> audit row)
    is verifiable without any hardware. Swap PRINT_BACKEND to `windows` on the
    shop PC and nothing else changes.
    """

    name = "dummy"

    def print_pdf(self, pdf_bytes: bytes, filename: str, **context) -> PrintResult:
        spool_dir = Path(settings.PRINT_SPOOL_DIR)
        spool_dir.mkdir(parents=True, exist_ok=True)

        stamp = timezone.localtime().strftime("%Y%m%d-%H%M%S")
        target = spool_dir / f"{stamp}_{filename}"
        target.write_bytes(pdf_bytes)

        printer = self.printer_name or "<default printer>"
        message = (
            f"MOCK PRINT: {self.copies} copy(ies) of '{filename}' "
            f"({len(pdf_bytes) / 1024:.1f} KB) would have been sent to {printer}. "
            f"Saved a copy at {target}."
        )
        result = self._ok(message, spooled_file=str(target))
        result.mocked = True
        return result

    def list_printers(self) -> list[str]:
        return ["Mock-Thermal-80mm", "Mock-Laser-A4"]


# ---------------------------------------------------------------------------
# 2. Windows  --  the real thing on the shop PC
# ---------------------------------------------------------------------------
class WindowsPrintBackend(BasePrintBackend):
    """
    Silent printing on Windows, in order of preference:

    1. SumatraPDF ``-print-to`` / ``-print-to-default`` (set SUMATRA_PDF_PATH).
       Truly silent, no dialog, honours the copy count. This is the recommended
       setup for a shop counter.
    2. Adobe Reader ``/t`` if it happens to be installed.
    3. ``ShellExecute(..., "print", ...)`` via pywin32, which uses whatever app
       is registered for .pdf. Silent with most viewers, but not guaranteed.
    """

    name = "windows"

    ADOBE_CANDIDATES = [
        r"C:\Program Files\Adobe\Acrobat DC\Acrobat\Acrobat.exe",
        r"C:\Program Files (x86)\Adobe\Acrobat Reader DC\Reader\AcroRd32.exe",
    ]

    def is_available(self) -> bool:
        return platform.system() == "Windows"

    def print_pdf(self, pdf_bytes: bytes, filename: str, **context) -> PrintResult:
        if not self.is_available():
            return self._fail("The windows backend only runs on Windows.")

        tmp_path = self._write_temp(pdf_bytes, filename)
        try:
            sumatra = self._find_sumatra()
            if sumatra:
                return self._print_with_sumatra(sumatra, tmp_path, filename)

            adobe = next((p for p in self.ADOBE_CANDIDATES if os.path.exists(p)), None)
            if adobe:
                return self._print_with_adobe(adobe, tmp_path, filename)

            return self._print_with_shell(tmp_path, filename)
        except Exception as exc:  # pragma: no cover - hardware specific
            logger.exception("Windows printing failed")
            return self._fail(f"Windows printing failed: {exc}")

    # -- strategies --------------------------------------------------------
    def _print_with_sumatra(self, exe: str, path: Path, filename: str) -> PrintResult:
        cmd = [exe, "-silent", "-exit-when-done"]
        if self.printer_name:
            cmd += ["-print-to", self.printer_name]
        else:
            cmd += ["-print-to-default"]
        # SumatraPDF has no copies flag, so the job is submitted N times.
        cmd += ["-print-settings", "fit", str(path)]

        for copy_no in range(self.copies):
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
            if proc.returncode != 0:
                return self._fail(
                    f"SumatraPDF exited with {proc.returncode} on copy "
                    f"{copy_no + 1}: {proc.stderr.strip() or proc.stdout.strip()}"
                )
        return self._ok(
            f"Sent {self.copies} copy(ies) of '{filename}' to "
            f"{self.printer_name or 'the default printer'} via SumatraPDF.",
            spooled_file=str(path),
        )

    def _print_with_adobe(self, exe: str, path: Path, filename: str) -> PrintResult:
        for _ in range(self.copies):
            args = [exe, "/n", "/t", str(path)]
            if self.printer_name:
                args.append(self.printer_name)
            subprocess.Popen(args, close_fds=True)
        return self._ok(
            f"Handed '{filename}' to Adobe Reader for printing "
            f"({self.copies} copy(ies)).",
            spooled_file=str(path),
        )

    def _print_with_shell(self, path: Path, filename: str) -> PrintResult:
        try:
            import win32api  # type: ignore
            import win32print  # type: ignore
        except ImportError:
            return self._fail(
                "pywin32 is not installed and SumatraPDF was not found. "
                "Install pywin32 or set SUMATRA_PDF_PATH in .env."
            )

        printer = self.printer_name or win32print.GetDefaultPrinter()
        for _ in range(self.copies):
            win32api.ShellExecute(0, "print", str(path), f'/d:"{printer}"', ".", 0)
        return self._ok(
            f"Sent '{filename}' to {printer} through the Windows shell "
            f"({self.copies} copy(ies)). Install SumatraPDF for guaranteed "
            f"dialog-free printing.",
            spooled_file=str(path),
        )

    # -- helpers -----------------------------------------------------------
    def _find_sumatra(self) -> str | None:
        configured = settings.SUMATRA_PDF_PATH
        if configured and os.path.exists(configured):
            return configured
        on_path = shutil.which("SumatraPDF") or shutil.which("SumatraPDF.exe")
        if on_path:
            return on_path
        for candidate in (
            r"C:\Program Files\SumatraPDF\SumatraPDF.exe",
            r"C:\Program Files (x86)\SumatraPDF\SumatraPDF.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\SumatraPDF\SumatraPDF.exe"),
        ):
            if os.path.exists(candidate):
                return candidate
        return None

    def _write_temp(self, pdf_bytes: bytes, filename: str) -> Path:
        spool_dir = Path(settings.PRINT_SPOOL_DIR)
        spool_dir.mkdir(parents=True, exist_ok=True)
        path = spool_dir / filename
        path.write_bytes(pdf_bytes)
        return path

    def list_printers(self) -> list[str]:
        try:
            import win32print  # type: ignore
        except ImportError:
            return []
        flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
        return [p[2] for p in win32print.EnumPrinters(flags)]


# ---------------------------------------------------------------------------
# 3. CUPS (Linux / macOS)
# ---------------------------------------------------------------------------
class CupsPrintBackend(BasePrintBackend):
    """Silent printing through the `lp` command."""

    name = "cups"

    def is_available(self) -> bool:
        return shutil.which("lp") is not None

    def print_pdf(self, pdf_bytes: bytes, filename: str, **context) -> PrintResult:
        if not self.is_available():
            return self._fail("`lp` was not found. Is CUPS installed?")

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as fh:
            fh.write(pdf_bytes)
            tmp = fh.name

        cmd = ["lp", "-n", str(self.copies)]
        if self.printer_name:
            cmd += ["-d", self.printer_name]
        cmd.append(tmp)

        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        if proc.returncode != 0:
            return self._fail(f"lp failed: {proc.stderr.strip()}")
        return self._ok(
            f"Queued '{filename}' with CUPS: {proc.stdout.strip()}", spooled_file=tmp
        )

    def list_printers(self) -> list[str]:
        if not shutil.which("lpstat"):
            return []
        proc = subprocess.run(["lpstat", "-a"], capture_output=True, text=True)
        return [line.split()[0] for line in proc.stdout.splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# 4. ESC/POS thermal printers
# ---------------------------------------------------------------------------
class EscposPrintBackend(BasePrintBackend):
    """
    Direct to a receipt printer over network / USB / device file.

    Thermal printers do not consume PDF, so this backend renders the invoice as
    plain text from the `context["invoice"]` object instead of the PDF bytes.
    """

    name = "escpos"
    WIDTH = 42  # characters on a typical 80mm roll

    def is_available(self) -> bool:
        try:
            import escpos  # noqa: F401
        except ImportError:
            return False
        return True

    def _connect(self):
        from escpos import printer as escpos_printer

        mode = settings.ESCPOS_CONNECTION
        if mode == "network":
            return escpos_printer.Network(settings.ESCPOS_HOST, port=settings.ESCPOS_PORT)
        if mode == "usb":
            return escpos_printer.Usb(
                int(settings.ESCPOS_USB_VENDOR_ID, 16),
                int(settings.ESCPOS_USB_PRODUCT_ID, 16),
            )
        return escpos_printer.File(settings.ESCPOS_DEVICE_FILE)

    def print_pdf(self, pdf_bytes: bytes, filename: str, **context) -> PrintResult:
        if not self.is_available():
            return self._fail("python-escpos is not installed.")
        invoice = context.get("invoice")
        if invoice is None:
            return self._fail("The escpos backend needs the invoice object.")

        try:
            device = self._connect()
        except Exception as exc:  # pragma: no cover - hardware specific
            return self._fail(f"Could not reach the thermal printer: {exc}")

        try:
            for _ in range(self.copies):
                device.text(self._render_receipt(invoice))
                device.cut()
            return self._ok(f"Printed receipt for {invoice.number} on the thermal printer.")
        except Exception as exc:  # pragma: no cover
            return self._fail(f"Thermal printing failed: {exc}")
        finally:
            try:
                device.close()
            except Exception:
                pass

    def _render_receipt(self, invoice) -> str:
        from billing.models import ShopProfile

        shop = ShopProfile.load()
        w = self.WIDTH
        sep = "-" * w
        lines = [
            shop.name.center(w),
            *[line.center(w) for line in shop.address_block],
            (f"Ph: {shop.phone}".center(w) if shop.phone else ""),
            sep,
            f"Bill : {invoice.number}",
            f"Date : {invoice.invoice_date:%d-%m-%Y}",
            f"Name : {invoice.customer_name}",
            f"Phone: {invoice.customer_phone}",
            f"City : {invoice.customer_city}",
            sep,
        ]
        for item in invoice.items.all():
            lines.append(item.description[: w - 1])
            lines.append(f"  {item.quantity} x {item.rate}".ljust(w - 12)
                         + f"{item.gross_amount:>12}")
            if item.has_discount:
                lines.append(f"  Discount {item.discount_label}".ljust(w - 12)
                             + f"{-item.discount_amount:>12}")
                lines.append("".ljust(w - 12) + f"{item.net_amount:>12}")
        lines += [
            sep,
            "Subtotal".ljust(w - 12) + f"{invoice.subtotal:>12}",
        ]
        if invoice.total_discount > 0:
            lines.append("Discount".ljust(w - 12) + f"{-invoice.total_discount:>12}")
        lines += [
            "TOTAL".ljust(w - 12) + f"{invoice.grand_total:>12}",
            "Paid".ljust(w - 12) + f"{invoice.paid_amount:>12}",
        ]
        if invoice.amount_pending:
            lines.append("Pending".ljust(w - 12) + f"{invoice.amount_pending:>12}")
            if invoice.due_date:
                lines.append(f"Due date: {invoice.due_date:%d-%m-%Y}")
        if invoice.amount_overpaid:
            lines.append("Overpaid".ljust(w - 12) + f"{invoice.amount_overpaid:>12}")
        lines += [sep, shop.invoice_footer_note.center(w), "\n"]
        return "\n".join(line for line in lines if line is not None) + "\n"


# ---------------------------------------------------------------------------
# 5. Browser dialog  --  fallback for remote hosting
# ---------------------------------------------------------------------------
class BrowserPrintBackend(BasePrintBackend):
    """
    Does not print server-side; tells the view to trigger window.print().

    Use this when the app is NOT on the same machine as the printer. Launching
    Chrome with `--kiosk-printing` makes even this path dialog-free.
    """

    name = "browser"

    def print_pdf(self, pdf_bytes: bytes, filename: str, **context) -> PrintResult:
        result = self._ok(
            "Opening the browser print dialog. Start Chrome with "
            "--kiosk-printing to skip the dialog entirely."
        )
        result.needs_browser_dialog = True
        return result
