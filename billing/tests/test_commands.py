"""Management command tests."""
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from billing.models import Customer, Invoice, InvoiceLineItem, PaymentStatus, ShopProfile


class DocxTemplateCommandTests(TestCase):
    def test_list_prints_the_placeholders(self):
        out = StringIO()
        call_command("docx_template", "--list", stdout=out)
        text = out.getvalue()
        for expected in [
            "{{ invoice_number }}",
            "{{ invoice_date }}",
            "{{ customer_name }}",
            "{{ customer_phone }}",
            "{{ customer_city }}",
            "{{ grand_total }}",
            "{{ pending_amount }}",
            "{{ due_date }}",
            "{%tr for item in items %}",
        ]:
            self.assertIn(expected, text)

    def test_create_writes_a_usable_template(self):
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "bill.docx"
            call_command("docx_template", "--create", "--path", str(target), stdout=StringIO())

            self.assertTrue(target.exists())
            self.assertGreater(target.stat().st_size, 5000)

            from docxtpl import DocxTemplate

            variables = DocxTemplate(str(target)).get_undeclared_template_variables()
            for expected in ["invoice_number", "invoice_date", "customer_name", "items"]:
                self.assertIn(expected, variables)

    def test_create_refuses_to_overwrite_without_force(self):
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "bill.docx"
            call_command("docx_template", "--create", "--path", str(target), stdout=StringIO())
            with self.assertRaises(CommandError):
                call_command("docx_template", "--create", "--path", str(target), stdout=StringIO())

    def test_force_overwrites(self):
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "bill.docx"
            call_command("docx_template", "--create", "--path", str(target), stdout=StringIO())
            call_command(
                "docx_template", "--create", "--force", "--path", str(target), stdout=StringIO()
            )
            self.assertTrue(target.exists())

    def test_check_renders_the_generated_template_with_sample_data(self):
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "bill.docx"
            call_command("docx_template", "--create", "--path", str(target), stdout=StringIO())

            out = StringIO()
            call_command("docx_template", "--check", "--path", str(target), stdout=out)
            self.assertIn("Rendered OK", out.getvalue())

            rendered = target.with_name(f"{target.stem}__check.docx")
            self.assertTrue(rendered.exists())

            from docx import Document

            doc = Document(str(rendered))
            text = "\n".join(p.text for p in doc.paragraphs)
            for table in doc.tables:
                for row in table.rows:
                    text += "\n" + " | ".join(cell.text for cell in row.cells)

            # No Jinja tag may survive a successful render.
            self.assertNotIn("{{", text)
            self.assertNotIn("{%", text)
            self.assertIn("Cotton shirt", text)

    def test_check_on_a_missing_template_errors_clearly(self):
        with self.assertRaises(CommandError):
            call_command(
                "docx_template", "--check", "--path", "/nowhere/nope.docx", stdout=StringIO()
            )


@override_settings(DOCUMENT_ENGINE="html", PRINT_BACKEND="dummy")
class SeedDemoCommandTests(TestCase):
    def test_it_creates_a_login_shop_customers_and_bills(self):
        call_command("seed_demo", "--bills", "8", stdout=StringIO())

        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User.objects.get(email="owner@shop.test")
        self.assertTrue(user.check_password("billing123"))

        self.assertEqual(Invoice.objects.count(), 8)
        self.assertTrue(Customer.objects.exists())
        self.assertTrue(InvoiceLineItem.objects.exists())
        self.assertNotEqual(ShopProfile.load().name, "My Shop")

    def test_seeded_bills_have_consistent_money(self):
        call_command("seed_demo", "--bills", "12", stdout=StringIO())
        for invoice in Invoice.objects.prefetch_related("items"):
            gross = sum(item.gross_amount for item in invoice.items.all())
            discount = sum(item.discount_amount for item in invoice.items.all())
            self.assertEqual(invoice.subtotal, gross)
            self.assertEqual(invoice.total_discount, discount)
            self.assertEqual(invoice.grand_total, gross - discount)
            self.assertEqual(invoice.balance, invoice.grand_total - invoice.paid_amount)

    def test_seeded_bills_respect_the_payment_rules(self):
        call_command("seed_demo", "--bills", "20", stdout=StringIO())
        for invoice in Invoice.objects.all():
            with self.subTest(invoice=invoice.number, status=invoice.payment_status):
                if invoice.payment_status == PaymentStatus.PAID:
                    self.assertEqual(invoice.paid_amount, invoice.grand_total)
                    self.assertIsNone(invoice.due_date)
                elif invoice.payment_status == PaymentStatus.PENDING:
                    self.assertEqual(invoice.paid_amount, 0)
                    self.assertIsNotNone(invoice.due_date)
                elif invoice.payment_status == PaymentStatus.UNDERPAID:
                    self.assertLess(invoice.paid_amount, invoice.grand_total)
                    self.assertIsNotNone(invoice.due_date)
                else:
                    self.assertGreater(invoice.paid_amount, invoice.grand_total)
                    self.assertIsNone(invoice.due_date)

    def test_reset_clears_previous_data(self):
        call_command("seed_demo", "--bills", "5", stdout=StringIO())
        call_command("seed_demo", "--bills", "3", "--reset", stdout=StringIO())
        self.assertEqual(Invoice.objects.count(), 3)

    def test_seeding_is_deterministic_for_a_given_seed(self):
        call_command("seed_demo", "--bills", "5", "--seed", "42", stdout=StringIO())
        first = list(Invoice.objects.order_by("id").values_list("grand_total", flat=True))

        call_command("seed_demo", "--bills", "5", "--seed", "42", "--reset", stdout=StringIO())
        second = list(Invoice.objects.order_by("id").values_list("grand_total", flat=True))

        self.assertEqual(first, second)

    def test_clear_empties_the_book_without_seeding(self):
        call_command("seed_demo", "--bills", "5", stdout=StringIO())
        self.assertEqual(Invoice.objects.count(), 5)

        out = StringIO()
        call_command("seed_demo", "--clear", stdout=out)

        self.assertEqual(Invoice.objects.count(), 0)
        self.assertEqual(Customer.objects.count(), 0)
        self.assertEqual(InvoiceLineItem.objects.count(), 0)
        self.assertIn("book is empty", out.getvalue())

        # The login and the shop letterhead survive.
        from django.contrib.auth import get_user_model

        self.assertTrue(get_user_model().objects.filter(email="owner@shop.test").exists())
        self.assertNotEqual(ShopProfile.load().name, "My Shop")

    def test_numbering_restarts_after_clear(self):
        call_command("seed_demo", "--bills", "3", stdout=StringIO())
        call_command("seed_demo", "--clear", stdout=StringIO())
        call_command("seed_demo", "--bills", "1", stdout=StringIO())
        self.assertTrue(Invoice.objects.get().number.endswith("/0001"))
