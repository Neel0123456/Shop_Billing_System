"""Model-level tests: the money maths, numbering and payment derivation."""
from datetime import date, timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from billing.models import (
    Customer,
    DiscountType,
    Invoice,
    InvoiceLineItem,
    InvoiceSequence,
    PaymentStatus,
    ShopProfile,
    money,
)

from .factories import make_customer, make_invoice, make_shop, make_user


class MoneyHelperTests(TestCase):
    def test_rounds_half_up_not_bankers(self):
        # Python's round() would give 2.66 here; a shopkeeper expects 2.68.
        self.assertEqual(money("2.675"), Decimal("2.68"))
        self.assertEqual(money("0.125"), Decimal("0.13"))

    def test_blank_and_none_become_zero(self):
        self.assertEqual(money(None), Decimal("0.00"))
        self.assertEqual(money(""), Decimal("0.00"))

    def test_always_two_decimal_places(self):
        self.assertEqual(str(money(5)), "5.00")
        self.assertEqual(str(money(Decimal("1.1"))), "1.10")


class LineItemCalculationTests(TestCase):
    def setUp(self):
        self.invoice = make_invoice(items=[{"description": "Base", "rate": "100.00"}])

    def _line(self, **kwargs):
        defaults = {
            "invoice": self.invoice,
            "description": "Test item",
            "quantity": Decimal("1"),
            "rate": Decimal("1000.00"),
            "discount_type": DiscountType.PERCENT,
            "discount_value": Decimal("0"),
        }
        defaults.update(kwargs)
        return InvoiceLineItem(**defaults).compute()

    def test_no_discount_leaves_gross_untouched(self):
        line = self._line()
        self.assertEqual(line.gross_amount, Decimal("1000.00"))
        self.assertEqual(line.discount_amount, Decimal("0.00"))
        self.assertEqual(line.net_amount, Decimal("1000.00"))
        self.assertFalse(line.has_discount)
        self.assertEqual(line.discount_label, "")

    def test_percent_discount(self):
        line = self._line(discount_value=Decimal("10"))
        self.assertEqual(line.discount_amount, Decimal("100.00"))
        self.assertEqual(line.net_amount, Decimal("900.00"))
        self.assertEqual(line.discount_label, "-10% (-100.00)")

    def test_amount_discount_prints_as_a_subtraction(self):
        line = self._line(discount_type=DiscountType.AMOUNT, discount_value=Decimal("150"))
        self.assertEqual(line.discount_amount, Decimal("150.00"))
        self.assertEqual(line.net_amount, Decimal("850.00"))
        self.assertEqual(line.discount_label, "-150.00")

    def test_quantity_multiplies_the_rate(self):
        line = self._line(quantity=Decimal("3"), rate=Decimal("250.50"))
        self.assertEqual(line.gross_amount, Decimal("751.50"))

    def test_percent_discount_is_capped_at_100(self):
        line = self._line(discount_value=Decimal("150"))
        self.assertEqual(line.discount_amount, Decimal("1000.00"))
        self.assertEqual(line.net_amount, Decimal("0.00"))

    def test_amount_discount_can_never_make_the_line_negative(self):
        line = self._line(discount_type=DiscountType.AMOUNT, discount_value=Decimal("5000"))
        self.assertEqual(line.discount_amount, Decimal("1000.00"))
        self.assertEqual(line.net_amount, Decimal("0.00"))

    def test_zero_discount_is_never_displayed(self):
        for dtype in (DiscountType.PERCENT, DiscountType.AMOUNT):
            line = self._line(discount_type=dtype, discount_value=Decimal("0"))
            self.assertEqual(line.discount_label, "", dtype)
            self.assertFalse(line.has_discount, dtype)

    def test_negative_discount_is_ignored_by_compute(self):
        # The form rejects it; compute() must still not invent a surcharge.
        line = self._line(discount_value=Decimal("-25"))
        self.assertEqual(line.discount_amount, Decimal("0.00"))
        self.assertEqual(line.net_amount, Decimal("1000.00"))

    def test_percent_rounding_uses_half_up(self):
        line = self._line(rate=Decimal("333.33"), discount_value=Decimal("7.5"))
        # 333.33 * 0.075 = 24.99975 -> 25.00
        self.assertEqual(line.discount_amount, Decimal("25.00"))
        self.assertEqual(line.net_amount, Decimal("308.33"))

    def test_save_recomputes_stored_columns(self):
        line = InvoiceLineItem.objects.create(
            invoice=self.invoice,
            description="Saved item",
            quantity=Decimal("2"),
            rate=Decimal("300"),
            discount_type=DiscountType.AMOUNT,
            discount_value=Decimal("100"),
        )
        line.refresh_from_db()
        self.assertEqual(line.gross_amount, Decimal("600.00"))
        self.assertEqual(line.net_amount, Decimal("500.00"))


class InvoiceTotalsTests(TestCase):
    def test_totals_roll_up_from_lines(self):
        invoice = make_invoice(
            items=[
                {"description": "A", "quantity": 2, "rate": "750.00",
                 "discount_type": DiscountType.PERCENT, "discount_value": "10"},
                {"description": "B", "rate": "1450.00",
                 "discount_type": DiscountType.AMOUNT, "discount_value": "150"},
                {"description": "C", "quantity": 3, "rate": "120.00"},
            ]
        )
        # gross: 1500 + 1450 + 360 = 3310 ; discount: 150 + 150 = 300
        self.assertEqual(invoice.subtotal, Decimal("3310.00"))
        self.assertEqual(invoice.total_discount, Decimal("300.00"))
        self.assertEqual(invoice.grand_total, Decimal("3010.00"))

    def test_recalculate_is_idempotent(self):
        invoice = make_invoice(items=[{"description": "A", "rate": "99.99"}])
        first = invoice.grand_total
        invoice.recalculate()
        invoice.refresh_from_db()
        self.assertEqual(invoice.grand_total, first)


class PaymentStatusDerivationTests(TestCase):
    def test_paid_sets_paid_to_total_and_clears_due_date(self):
        invoice = make_invoice(
            items=[{"description": "A", "rate": "1000"}],
            payment_status=PaymentStatus.PAID,
            due_date=date(2030, 1, 1),
        )
        self.assertEqual(invoice.paid_amount, Decimal("1000.00"))
        self.assertEqual(invoice.balance, Decimal("0.00"))
        self.assertIsNone(invoice.due_date)
        self.assertEqual(invoice.amount_pending, Decimal("0.00"))
        self.assertEqual(invoice.amount_overpaid, Decimal("0.00"))
        self.assertFalse(invoice.is_outstanding)

    def test_pending_zeroes_the_paid_amount_and_keeps_the_due_date(self):
        due = timezone.localdate() + timedelta(days=7)
        invoice = make_invoice(
            items=[{"description": "A", "rate": "1000"}],
            payment_status=PaymentStatus.PENDING,
            paid_amount="500",
            due_date=due,
        )
        self.assertEqual(invoice.paid_amount, Decimal("0.00"))
        self.assertEqual(invoice.amount_pending, Decimal("1000.00"))
        self.assertEqual(invoice.due_date, due)
        self.assertTrue(invoice.is_outstanding)

    def test_underpaid_reports_the_shortfall(self):
        invoice = make_invoice(
            items=[{"description": "A", "rate": "1000"}],
            payment_status=PaymentStatus.UNDERPAID,
            paid_amount="400",
            due_date=timezone.localdate() + timedelta(days=3),
        )
        self.assertEqual(invoice.balance, Decimal("600.00"))
        self.assertEqual(invoice.amount_pending, Decimal("600.00"))
        self.assertEqual(invoice.amount_overpaid, Decimal("0.00"))

    def test_overpaid_reports_the_excess_and_clears_the_due_date(self):
        invoice = make_invoice(
            items=[{"description": "A", "rate": "1000"}],
            payment_status=PaymentStatus.OVERPAID,
            paid_amount="1200",
            due_date=timezone.localdate(),
        )
        self.assertEqual(invoice.balance, Decimal("-200.00"))
        self.assertEqual(invoice.amount_overpaid, Decimal("200.00"))
        self.assertEqual(invoice.amount_pending, Decimal("0.00"))
        self.assertIsNone(invoice.due_date)

    def test_is_overdue_only_for_outstanding_bills_past_the_due_date(self):
        past = timezone.localdate() - timedelta(days=1)
        overdue = make_invoice(
            items=[{"description": "A", "rate": "100"}],
            payment_status=PaymentStatus.PENDING,
            due_date=past,
        )
        self.assertTrue(overdue.is_overdue)

        paid = make_invoice(
            customer=make_customer(phone="9000000001"),
            items=[{"description": "A", "rate": "100"}],
            payment_status=PaymentStatus.PAID,
        )
        self.assertFalse(paid.is_overdue)


class InvoiceNumberingTests(TestCase):
    def test_financial_year_boundary(self):
        self.assertEqual(InvoiceSequence.financial_year(date(2026, 3, 31)), "2025-26")
        self.assertEqual(InvoiceSequence.financial_year(date(2026, 4, 1)), "2026-27")
        self.assertEqual(InvoiceSequence.financial_year(date(2026, 12, 31)), "2026-27")

    def test_numbers_increment_within_a_year(self):
        first = InvoiceSequence.next_number(date(2026, 5, 1))
        second = InvoiceSequence.next_number(date(2026, 6, 1))
        self.assertEqual(first, "INV/2026-27/0001")
        self.assertEqual(second, "INV/2026-27/0002")

    def test_counter_restarts_each_financial_year(self):
        InvoiceSequence.next_number(date(2026, 3, 1))
        fresh = InvoiceSequence.next_number(date(2026, 4, 1))
        self.assertEqual(fresh, "INV/2026-27/0001")

    def test_invoice_gets_a_number_automatically(self):
        invoice = make_invoice()
        self.assertTrue(invoice.number.startswith("INV/"))

    def test_numbers_are_unique(self):
        first = make_invoice()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Invoice.objects.create(
                    number=first.number,
                    customer=first.customer,
                    customer_name="Dup",
                    customer_phone="9000000002",
                    customer_city="Himmatnagar",
                )


class CustomerTests(TestCase):
    def test_city_falls_back_to_the_shop_default(self):
        customer = Customer.objects.create(name="No City", phone="9000000003")
        self.assertEqual(customer.city, "Himmatnagar")

    def test_supplied_city_overrides_the_default(self):
        customer = Customer.objects.create(name="Traveller", phone="9000000004", city="Ahmedabad")
        self.assertEqual(customer.city, "Ahmedabad")

    def test_email_is_lowercased(self):
        customer = Customer.objects.create(
            name="Case", phone="9000000005", email="MiXeD@Example.COM"
        )
        self.assertEqual(customer.email, "mixed@example.com")

    def test_phone_is_unique(self):
        make_customer(phone="9111111111")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_customer(phone="9111111111", name="Someone else")


class InvoiceSnapshotTests(TestCase):
    def test_editing_the_customer_does_not_change_an_issued_bill(self):
        customer = make_customer(name="Original Name", city="Himmatnagar")
        invoice = make_invoice(customer=customer)

        customer.name = "Renamed Later"
        customer.city = "Surat"
        customer.save()

        invoice.refresh_from_db()
        self.assertEqual(invoice.customer_name, "Original Name")
        self.assertEqual(invoice.customer_city, "Himmatnagar")

    def test_city_defaults_on_the_invoice_too(self):
        customer = make_customer(phone="9222222222")
        invoice = Invoice.objects.create(
            customer=customer,
            customer_name=customer.name,
            customer_phone=customer.phone,
            customer_city="",
        )
        self.assertEqual(invoice.customer_city, "Himmatnagar")

    def test_pdf_filename_is_filesystem_safe(self):
        invoice = make_invoice(customer=make_customer(name="A/B\\C: Traders"))
        self.assertNotIn("/", invoice.pdf_filename)
        self.assertNotIn("\\", invoice.pdf_filename)
        self.assertNotIn(":", invoice.pdf_filename)
        self.assertTrue(invoice.pdf_filename.endswith(".pdf"))

    def test_pdf_filename_is_db_id_then_customer_name(self):
        invoice = make_invoice(customer=make_customer(name="Ramesh Patel"))
        self.assertEqual(invoice.pdf_filename, f"{invoice.pk}_Ramesh_Patel.pdf")

    def test_pdf_filename_is_unique_even_for_identical_names(self):
        first = make_invoice(customer=make_customer(name="Same Name", phone="9700000001"))
        second = make_invoice(customer=make_customer(name="Same Name", phone="9700000002"))
        self.assertNotEqual(first.pdf_filename, second.pdf_filename)

    def test_bill_date_defaults_to_today(self):
        invoice = make_invoice()
        self.assertEqual(invoice.invoice_date, timezone.localdate())

    def test_created_at_is_stored(self):
        invoice = make_invoice()
        self.assertIsNotNone(invoice.created_at)
        self.assertEqual(
            timezone.localtime(invoice.created_at).date(), timezone.localdate()
        )


class QuerySetTests(TestCase):
    def setUp(self):
        self.paid = make_invoice(
            customer=make_customer(phone="9300000001"),
            items=[{"description": "A", "rate": "100"}],
            payment_status=PaymentStatus.PAID,
        )
        self.pending = make_invoice(
            customer=make_customer(phone="9300000002"),
            items=[{"description": "A", "rate": "200"}],
            payment_status=PaymentStatus.PENDING,
            due_date=timezone.localdate() - timedelta(days=2),
        )
        self.underpaid = make_invoice(
            customer=make_customer(phone="9300000003"),
            items=[{"description": "A", "rate": "300"}],
            payment_status=PaymentStatus.UNDERPAID,
            paid_amount="100",
            due_date=timezone.localdate() + timedelta(days=5),
        )

    def test_outstanding_selects_pending_and_underpaid(self):
        found = set(Invoice.objects.outstanding().values_list("pk", flat=True))
        self.assertEqual(found, {self.pending.pk, self.underpaid.pk})

    def test_overdue_only_selects_past_due_dates(self):
        found = list(Invoice.objects.overdue().values_list("pk", flat=True))
        self.assertEqual(found, [self.pending.pk])


class ShopProfileTests(TestCase):
    def test_is_a_singleton(self):
        first = ShopProfile.load()
        second = ShopProfile.load()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(ShopProfile.objects.count(), 1)

    def test_saving_a_new_instance_overwrites_row_one(self):
        make_shop(name="First Name")
        ShopProfile(name="Second Name").save()
        self.assertEqual(ShopProfile.objects.count(), 1)
        self.assertEqual(ShopProfile.load().name, "Second Name")

    def test_address_block_skips_blank_lines(self):
        shop = make_shop(address_line1="Line 1", address_line2="", pincode="383001")
        self.assertEqual(shop.address_block, ["Line 1", "Himmatnagar, Gujarat - 383001"])


class PrintCounterTests(TestCase):
    def test_mark_printed_increments_and_stamps(self):
        invoice = make_invoice()
        self.assertEqual(invoice.print_count, 0)
        invoice.mark_printed()
        self.assertEqual(invoice.print_count, 1)
        self.assertIsNotNone(invoice.last_printed_at)
        invoice.mark_printed()
        self.assertEqual(invoice.print_count, 2)
