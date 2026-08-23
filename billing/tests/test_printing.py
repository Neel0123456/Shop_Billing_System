"""
Printing and document-pipeline tests.

These verify the whole chain without any hardware:
  Word template -> filled .docx -> PDF -> print backend -> PrintJob row.
"""
import shutil
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase, override_settings

from billing.models import PrintJob
from billing.services import documents, docx_engine, html_engine
from billing.services.docx_convert import (
    DocxConversionError,
    converter_status,
    find_libreoffice,
    word_available,
)
from billing.services.printing import BACKENDS, available_backends, get_backend
from billing.services.printing.backends import (
    BrowserPrintBackend,
    CupsPrintBackend,
    DummyPrintBackend,
    EscposPrintBackend,
    WindowsPrintBackend,
)

from .factories import make_invoice, make_shop, make_user

MINIMAL_PDF = b"%PDF-1.4\n%%EOF\n"


class BackendRegistryTests(TestCase):
    def test_every_documented_backend_is_registered(self):
        for name in ["dummy", "windows", "cups", "escpos", "browser"]:
            self.assertIn(name, BACKENDS)

    @override_settings(PRINT_BACKEND="dummy")
    def test_get_backend_returns_the_configured_one(self):
        self.assertIsInstance(get_backend(), DummyPrintBackend)

    @override_settings(PRINT_BACKEND="browser")
    def test_get_backend_honours_the_setting(self):
        self.assertIsInstance(get_backend(), BrowserPrintBackend)

    @override_settings(PRINT_BACKEND="not-a-backend")
    def test_unknown_backend_fails_loudly(self):
        with self.assertRaises(ImproperlyConfigured):
            get_backend()

    def test_explicit_name_overrides_the_setting(self):
        self.assertIsInstance(get_backend("browser"), BrowserPrintBackend)

    @override_settings(PRINTER_NAME="Counter-Laser", PRINT_COPIES=3)
    def test_printer_name_and_copies_are_passed_through(self):
        backend = get_backend()
        self.assertEqual(backend.printer_name, "Counter-Laser")
        self.assertEqual(backend.copies, 3)

    def test_copies_never_drops_below_one(self):
        self.assertEqual(get_backend("dummy", copies=0).copies, 1)

    def test_unusable_backend_falls_back_to_the_mock(self):
        # cups is not present on Windows, so the till must keep working.
        backend = get_backend("cups")
        if not CupsPrintBackend().is_available():
            self.assertIsInstance(backend, DummyPrintBackend)

    def test_availability_report_covers_every_backend(self):
        report = available_backends()
        self.assertIn("windows", report)
        self.assertIn("dummy", report)
        self.assertTrue(report["dummy"])
        self.assertTrue(all(isinstance(v, bool) for v in report.values()))


class DummyBackendTests(TestCase):
    """The mock stands in for the printer while none is attached."""

    def setUp(self):
        make_shop()
        self.spool = Path(settings.PRINT_SPOOL_DIR)
        if self.spool.exists():
            shutil.rmtree(self.spool)

    def test_it_reports_success_and_flags_itself_as_mocked(self):
        result = DummyPrintBackend().print_pdf(MINIMAL_PDF, "INV-0001.pdf")
        self.assertTrue(result.success)
        self.assertTrue(result.mocked)
        self.assertEqual(result.backend, "dummy")
        self.assertEqual(result.db_status, PrintJob.Status.MOCKED)

    def test_it_writes_the_exact_bytes_it_would_have_printed(self):
        result = DummyPrintBackend().print_pdf(MINIMAL_PDF, "INV-0002.pdf")
        spooled = Path(result.spooled_file)
        self.assertTrue(spooled.exists())
        self.assertEqual(spooled.read_bytes(), MINIMAL_PDF)
        self.assertIn("INV-0002.pdf", spooled.name)

    def test_the_message_names_the_printer_and_copy_count(self):
        result = DummyPrintBackend(printer_name="Thermal-80", copies=2).print_pdf(
            MINIMAL_PDF, "INV-0003.pdf"
        )
        self.assertIn("Thermal-80", result.message)
        self.assertIn("2 copy", result.message)

    def test_it_lists_fake_printers(self):
        self.assertTrue(DummyPrintBackend().list_printers())


class BrowserBackendTests(TestCase):
    def test_it_asks_the_view_to_open_the_dialog(self):
        result = BrowserPrintBackend().print_pdf(MINIMAL_PDF, "INV.pdf")
        self.assertTrue(result.success)
        self.assertTrue(result.needs_browser_dialog)
        self.assertIn("kiosk-printing", result.message)


class WindowsBackendTests(TestCase):
    """
    Behaviour checks only: no job is actually submitted to the spooler.

    The real hardware path is exercised by flipping PRINT_BACKEND=windows on
    the shop PC and using the "Send a test print" button.
    """

    def test_it_reports_availability_per_platform(self):
        import platform

        backend = WindowsPrintBackend()
        self.assertEqual(backend.is_available(), platform.system() == "Windows")

    def test_sumatra_lookup_never_raises(self):
        # Returns a path or None depending on the machine; must not explode.
        result = WindowsPrintBackend()._find_sumatra()
        self.assertTrue(result is None or isinstance(result, str))

    def test_printer_enumeration_returns_a_list(self):
        self.assertIsInstance(WindowsPrintBackend().list_printers(), list)

    def test_it_writes_the_pdf_before_printing(self):
        backend = WindowsPrintBackend()
        path = backend._write_temp(MINIMAL_PDF, "temp-check.pdf")
        try:
            self.assertTrue(path.exists())
            self.assertEqual(path.read_bytes(), MINIMAL_PDF)
        finally:
            path.unlink(missing_ok=True)


class EscposBackendTests(TestCase):
    def setUp(self):
        make_shop()

    def test_it_needs_the_invoice_object(self):
        backend = EscposPrintBackend()
        if not backend.is_available():
            self.skipTest("python-escpos is not installed")
        result = backend.print_pdf(MINIMAL_PDF, "INV.pdf")
        self.assertFalse(result.success)
        self.assertIn("invoice", result.message.lower())

    def test_receipt_text_contains_the_bill_details(self):
        invoice = make_invoice(
            items=[
                {"description": "Shirt", "quantity": 2, "rate": "750",
                 "discount_value": "10"},
                {"description": "Socks", "rate": "120"},
            ]
        )
        text = EscposPrintBackend()._render_receipt(invoice)
        self.assertIn(invoice.number, text)
        self.assertIn("Shirt", text)
        self.assertIn("Socks", text)
        self.assertIn(invoice.customer_name, text)
        self.assertIn(invoice.invoice_date.strftime("%d-%m-%Y"), text)


class HtmlEngineTests(TestCase):
    def setUp(self):
        make_shop()
        self.invoice = make_invoice(
            items=[
                {"description": "Cotton shirt", "quantity": 2, "rate": "750",
                 "discount_value": "10"},
                {"description": "Plain socks", "rate": "120"},
            ]
        )

    def test_it_produces_a_real_pdf(self):
        pdf = html_engine.render_pdf(self.invoice)
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 800)

    def test_the_html_contains_the_bill_essentials(self):
        html = html_engine.render_html(self.invoice)
        for expected in [
            self.invoice.number,
            self.invoice.customer_name,
            self.invoice.customer_phone,
            self.invoice.customer_city,
            self.invoice.invoice_date.strftime("%d-%m-%Y"),
            "Cotton shirt",
            "Plain socks",
            "-10% (-150.00)",
        ]:
            self.assertIn(expected, html, expected)

    def test_a_zero_discount_row_prints_no_discount_text(self):
        html = html_engine.render_html(self.invoice)
        # "Plain socks" has no discount: the cell must be empty for it.
        self.assertNotIn("-0.00", html)
        self.assertNotIn("-0%", html)

    def test_link_callback_leaves_unknown_urls_alone(self):
        self.assertEqual(
            html_engine.link_callback("https://example.com/x.png", None),
            "https://example.com/x.png",
        )


class DocxEngineTests(TestCase):
    """The Word pipeline, using the template shipped by `docx_template --create`."""

    def setUp(self):
        make_shop()
        self.invoice = make_invoice(
            items=[
                {"description": "Cotton shirt", "quantity": 2, "rate": "750",
                 "discount_value": "10"},
                {"description": "Plain socks", "rate": "120"},
            ]
        )

    def test_template_path_comes_from_settings(self):
        self.assertEqual(docx_engine.template_path(), Path(settings.DOCX_TEMPLATE_PATH))

    @override_settings(DOCX_TEMPLATE_PATH=Path("/nowhere/missing.docx"))
    def test_a_missing_template_raises_a_helpful_error(self):
        with self.assertRaises(docx_engine.TemplateMissingError) as ctx:
            docx_engine.render_docx(self.invoice, Path("/tmp/out.docx"))
        self.assertIn("docx_template --create", str(ctx.exception))

    def test_filling_the_template_produces_a_docx_with_the_bill_data(self):
        if not docx_engine.template_exists():
            self.skipTest("run: python manage.py docx_template --create")

        out = Path(settings.INVOICE_DOCX_DIR) / "render-test.docx"
        docx_engine.render_docx(self.invoice, out)
        try:
            self.assertTrue(out.exists())

            from docx import Document

            doc = Document(str(out))
            text = "\n".join(p.text for p in doc.paragraphs)
            for table in doc.tables:
                for row in table.rows:
                    text += "\n" + " | ".join(cell.text for cell in row.cells)

            self.assertIn(self.invoice.number, text)
            self.assertIn(self.invoice.customer_name, text)
            self.assertIn(self.invoice.invoice_date.strftime("%d-%m-%Y"), text)
            self.assertIn("Cotton shirt", text)
            self.assertIn("Plain socks", text)
            # The percentage discount is rendered, the zero one is not.
            self.assertIn("-10%", text)
            self.assertNotIn("-0%", text)
            # No unrendered Jinja tags may survive.
            self.assertNotIn("{{", text)
            self.assertNotIn("{%", text)
        finally:
            out.unlink(missing_ok=True)

    def test_every_line_item_gets_its_own_row(self):
        if not docx_engine.template_exists():
            self.skipTest("run: python manage.py docx_template --create")

        from .factories import make_customer

        invoice = make_invoice(
            customer=make_customer(phone="9555500001", name="Rows Tester"),
            items=[{"description": f"Item {n}", "rate": "100"} for n in range(1, 6)],
        )
        out = Path(settings.INVOICE_DOCX_DIR) / "rows-test.docx"
        docx_engine.render_docx(invoice, out)
        try:
            from docx import Document

            doc = Document(str(out))
            text = "\n".join(
                cell.text for table in doc.tables for row in table.rows for cell in row.cells
            )
            for n in range(1, 6):
                self.assertIn(f"Item {n}", text)
        finally:
            out.unlink(missing_ok=True)


class ConverterDiagnosticsTests(TestCase):
    def test_word_availability_is_a_bool(self):
        self.assertIsInstance(word_available(), bool)

    def test_libreoffice_lookup_never_raises(self):
        result = find_libreoffice()
        self.assertTrue(result is None or isinstance(result, str))

    def test_status_report_has_the_expected_keys(self):
        status = converter_status()
        for key in ["configured", "word_available", "libreoffice_path", "libreoffice_available"]:
            self.assertIn(key, status)

    @override_settings(DOCX_TO_PDF_CONVERTER="libreoffice", LIBREOFFICE_PATH="")
    def test_a_missing_converter_reports_a_clear_error(self):
        if find_libreoffice():
            self.skipTest("LibreOffice is installed on this machine")
        with self.assertRaises(DocxConversionError):
            from billing.services.docx_convert import convert_docx_to_pdf

            convert_docx_to_pdf(Path("nope.docx"), Path("nope.pdf"))


class DocumentPipelineTests(TestCase):
    def setUp(self):
        make_shop()
        self.user = make_user()
        self.invoice = make_invoice(items=[{"description": "A", "rate": "1000"}])

    @override_settings(DOCUMENT_ENGINE="html")
    def test_html_engine_is_used_when_configured(self):
        self.assertEqual(documents.active_engine(), "html")
        self.assertTrue(documents.build_pdf(self.invoice).startswith(b"%PDF"))

    @override_settings(DOCUMENT_ENGINE="docx", DOCX_TEMPLATE_PATH=Path("/nowhere/x.docx"))
    def test_a_missing_word_template_falls_back_to_html(self):
        self.assertEqual(documents.active_engine(), "html")
        self.assertTrue(documents.build_pdf(self.invoice).startswith(b"%PDF"))

    @override_settings(DOCUMENT_ENGINE="docx")
    def test_docx_engine_is_selected_when_the_template_exists(self):
        if not docx_engine.template_exists():
            self.skipTest("run: python manage.py docx_template --create")
        self.assertEqual(documents.active_engine(), "docx")

    @override_settings(DOCUMENT_ENGINE="html")
    def test_archive_writes_into_the_configured_folder(self):
        path = documents.archive_pdf(self.invoice)
        self.assertTrue(path.exists())
        self.assertTrue(str(path).startswith(str(settings.INVOICE_PDF_DIR)))
        self.assertEqual(path.name, self.invoice.pdf_filename)
        self.assertTrue(path.read_bytes().startswith(b"%PDF"))

    @override_settings(DOCUMENT_ENGINE="html", INVOICE_ARCHIVE_SUBFOLDER="date")
    def test_archive_creates_a_folder_named_for_the_bill_date(self):
        path = documents.archive_pdf(self.invoice)
        expected = self.invoice.invoice_date.strftime("%Y-%m-%d")
        self.assertEqual(path.parent.name, expected)
        self.assertTrue(path.parent.is_dir())

    @override_settings(DOCUMENT_ENGINE="html", INVOICE_ARCHIVE_SUBFOLDER="date")
    def test_the_date_folder_is_created_when_it_does_not_exist(self):
        import shutil

        folder = documents.archive_dir(self.invoice)
        if folder.exists():
            shutil.rmtree(folder)
        self.assertFalse(folder.exists())

        documents.archive_pdf(self.invoice)
        self.assertTrue(folder.is_dir())

    @override_settings(DOCUMENT_ENGINE="html", INVOICE_ARCHIVE_SUBFOLDER="date")
    def test_bills_from_different_days_land_in_different_folders(self):
        from datetime import timedelta

        from .factories import make_customer, make_invoice

        yesterday = self.invoice.invoice_date - timedelta(days=1)
        older = make_invoice(
            customer=make_customer(phone="9666600001"),
            items=[{"description": "A", "rate": "100"}],
            invoice_date=yesterday,
        )
        today_path = documents.archive_pdf(self.invoice)
        older_path = documents.archive_pdf(older)

        self.assertNotEqual(today_path.parent, older_path.parent)
        self.assertEqual(older_path.parent.name, yesterday.strftime("%Y-%m-%d"))

    @override_settings(DOCUMENT_ENGINE="html", INVOICE_ARCHIVE_SUBFOLDER="month")
    def test_archive_can_group_by_month(self):
        path = documents.archive_pdf(self.invoice)
        self.assertEqual(path.parent.name, self.invoice.invoice_date.strftime("%Y-%m"))

    @override_settings(DOCUMENT_ENGINE="html", INVOICE_ARCHIVE_SUBFOLDER="year")
    def test_archive_can_group_by_financial_year(self):
        from billing.models import InvoiceSequence

        path = documents.archive_pdf(self.invoice)
        year = InvoiceSequence.financial_year(self.invoice.invoice_date)
        self.assertEqual(path.parent.name, year)

    @override_settings(DOCUMENT_ENGINE="html", INVOICE_ARCHIVE_SUBFOLDER="none")
    def test_archive_can_be_flat(self):
        path = documents.archive_pdf(self.invoice)
        self.assertEqual(path.parent, Path(settings.INVOICE_PDF_DIR))

    # -- the path is recorded in the database ------------------------------
    @override_settings(DOCUMENT_ENGINE="html", INVOICE_ARCHIVE_SUBFOLDER="date")
    def test_the_full_pdf_path_is_saved_on_the_invoice(self):
        self.assertEqual(self.invoice.pdf_path, "")

        path = documents.archive_pdf(self.invoice)
        self.invoice.refresh_from_db()

        self.assertEqual(self.invoice.pdf_path, str(path))
        self.assertTrue(Path(self.invoice.pdf_path).is_absolute())
        self.assertTrue(self.invoice.pdf_exists)
        self.assertIsNotNone(self.invoice.pdf_saved_at)

    @override_settings(DOCUMENT_ENGINE="html", INVOICE_ARCHIVE_SUBFOLDER="date")
    def test_the_stored_path_includes_the_date_folder_and_filename(self):
        documents.archive_pdf(self.invoice)
        self.invoice.refresh_from_db()

        stored = Path(self.invoice.pdf_path)
        self.assertEqual(stored.name, self.invoice.pdf_filename)
        self.assertEqual(stored.parent.name, self.invoice.invoice_date.strftime("%Y-%m-%d"))
        self.assertEqual(self.invoice.pdf_folder, str(stored.parent))

    @override_settings(DOCUMENT_ENGINE="html", INVOICE_ARCHIVE_SUBFOLDER="date")
    def test_re_archiving_keeps_one_path_and_one_file(self):
        first = documents.archive_pdf(self.invoice)
        second = documents.archive_pdf(self.invoice)
        self.invoice.refresh_from_db()

        self.assertEqual(first, second)
        self.assertEqual(self.invoice.pdf_path, str(second))

    @override_settings(DOCUMENT_ENGINE="html", INVOICE_ARCHIVE_SUBFOLDER="date")
    def test_pdf_exists_is_false_once_the_file_is_removed(self):
        path = documents.archive_pdf(self.invoice)
        self.invoice.refresh_from_db()
        self.assertTrue(self.invoice.pdf_exists)

        path.unlink()
        self.assertFalse(self.invoice.pdf_exists)
        # The recorded path stays, so the loss is visible rather than silent.
        self.assertNotEqual(self.invoice.pdf_path, "")

    @override_settings(DOCUMENT_ENGINE="html", PRINT_BACKEND="dummy")
    def test_printing_also_records_the_path(self):
        documents.print_invoice(self.invoice, requested_by=self.user)
        self.invoice.refresh_from_db()
        self.assertTrue(self.invoice.pdf_path)
        self.assertTrue(self.invoice.pdf_exists)

    @override_settings(DOCUMENT_ENGINE="html", PRINT_BACKEND="dummy")
    def test_print_invoice_records_a_job_and_archives(self):
        job = documents.print_invoice(self.invoice, requested_by=self.user)
        self.assertTrue(job.succeeded)
        self.assertEqual(job.backend, "dummy")
        self.assertEqual(job.requested_by, self.user)
        self.assertEqual(job.status, PrintJob.Status.MOCKED)

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.print_count, 1)

    @override_settings(DOCUMENT_ENGINE="html", PRINT_BACKEND="dummy")
    def test_repeated_prints_accumulate(self):
        for _ in range(3):
            documents.print_invoice(self.invoice, requested_by=self.user)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.print_count, 3)
        self.assertEqual(self.invoice.print_jobs.count(), 3)

    @override_settings(DOCUMENT_ENGINE="html", PRINT_BACKEND="dummy")
    def test_a_pdf_failure_is_recorded_and_never_raises(self):
        from unittest.mock import patch

        with patch.object(html_engine, "render_pdf", side_effect=RuntimeError("boom")):
            job = documents.print_invoice(self.invoice, requested_by=self.user)

        self.assertFalse(job.succeeded)
        self.assertEqual(job.status, PrintJob.Status.FAILED)
        self.assertIn("boom", job.message)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.print_count, 0)

    @override_settings(DOCUMENT_ENGINE="html", PRINT_BACKEND="dummy")
    def test_copies_are_passed_to_the_backend(self):
        job = documents.print_invoice(self.invoice, requested_by=self.user, copies=4)
        self.assertEqual(job.copies, 4)

    def test_pipeline_status_reports_everything_the_settings_page_shows(self):
        status = documents.pipeline_status()
        for key in [
            "document_engine_configured",
            "document_engine_active",
            "docx_template_path",
            "docx_template_exists",
            "converter",
            "print_backend",
            "printer_name",
            "backends",
            "pdf_dir",
            "docx_dir",
            "spool_dir",
        ]:
            self.assertIn(key, status)
