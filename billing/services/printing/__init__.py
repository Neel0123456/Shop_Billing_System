"""
Print backend registry.

Usage from anywhere in the app::

    from billing.services.printing import get_backend

    result = get_backend().print_pdf(pdf_bytes, "INV-0001.pdf", invoice=invoice)
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .backends import (
    BrowserPrintBackend,
    CupsPrintBackend,
    DummyPrintBackend,
    EscposPrintBackend,
    WindowsPrintBackend,
)
from .base import BasePrintBackend, PrinterError, PrintResult

logger = logging.getLogger(__name__)

BACKENDS: dict[str, type[BasePrintBackend]] = {
    "dummy": DummyPrintBackend,
    "mock": DummyPrintBackend,  # alias
    "windows": WindowsPrintBackend,
    "cups": CupsPrintBackend,
    "escpos": EscposPrintBackend,
    "browser": BrowserPrintBackend,
}

__all__ = [
    "BACKENDS",
    "BasePrintBackend",
    "PrintResult",
    "PrinterError",
    "get_backend",
    "available_backends",
]


def get_backend(name: str | None = None, **overrides) -> BasePrintBackend:
    """
    Build the configured backend.

    Falls back to the dummy backend (with a loud log line) when the configured
    backend cannot run here, so a missing printer never takes the till offline.
    """
    name = (name or settings.PRINT_BACKEND or "dummy").lower()
    try:
        backend_cls = BACKENDS[name]
    except KeyError:
        raise ImproperlyConfigured(
            f"Unknown PRINT_BACKEND {name!r}. Choose one of: {', '.join(sorted(BACKENDS))}"
        ) from None

    backend = backend_cls(
        printer_name=overrides.pop("printer_name", settings.PRINTER_NAME),
        copies=overrides.pop("copies", settings.PRINT_COPIES),
        **overrides,
    )

    if not backend.is_available():
        logger.warning(
            "PRINT_BACKEND=%s is not usable on this machine (missing dependency "
            "or wrong OS). Falling back to the mock backend.",
            name,
        )
        return DummyPrintBackend(
            printer_name=settings.PRINTER_NAME, copies=settings.PRINT_COPIES
        )
    return backend


def available_backends() -> dict[str, bool]:
    """Map of backend name -> usable on this machine. Shown on the settings page."""
    result = {}
    for key, cls in BACKENDS.items():
        if key == "mock":
            continue
        try:
            result[key] = cls().is_available()
        except Exception:  # pragma: no cover - defensive
            result[key] = False
    return result
