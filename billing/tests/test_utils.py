"""Formatting helpers: Indian digit grouping, amount in words, template filters."""
from decimal import Decimal

from django.template import Context, Template
from django.test import TestCase

from billing.utils import amount_in_words, format_inr, trim_decimal

from .factories import make_invoice, make_shop


class FormatInrTests(TestCase):
    def test_plain_numbers(self):
        self.assertEqual(format_inr(0), "0.00")
        self.assertEqual(format_inr(5), "5.00")
        self.assertEqual(format_inr("99.9"), "99.90")
        self.assertEqual(format_inr(999), "999.00")

    def test_indian_grouping(self):
        # Last group of three, then groups of two.
        self.assertEqual(format_inr(1000), "1,000.00")
        self.assertEqual(format_inr(99999), "99,999.00")
        self.assertEqual(format_inr(100000), "1,00,000.00")
        self.assertEqual(format_inr(1234567.5), "12,34,567.50")
        self.assertEqual(format_inr(10000000), "1,00,00,000.00")

    def test_negatives_keep_the_sign_outside(self):
        self.assertEqual(format_inr(-1234.5), "-1,234.50")

    def test_rounding_is_half_up(self):
        self.assertEqual(format_inr("2.675"), "2.68")
        self.assertEqual(format_inr("0.005"), "0.01")

    def test_symbol_prefix(self):
        self.assertEqual(format_inr(1500, symbol="Rs."), "Rs. 1,500.00")

    def test_blank_input_is_zero(self):
        self.assertEqual(format_inr(None), "0.00")
        self.assertEqual(format_inr(""), "0.00")


class TrimDecimalTests(TestCase):
    def test_it_never_produces_exponent_notation(self):
        # Decimal("10.00").normalize() is 1E+1 - it must not reach a bill.
        self.assertEqual(trim_decimal(Decimal("10.00")), "10")
        self.assertEqual(trim_decimal(Decimal("100.00")), "100")
        self.assertEqual(trim_decimal(Decimal("1000.00")), "1000")

    def test_it_keeps_meaningful_decimals(self):
        self.assertEqual(trim_decimal(Decimal("2.50")), "2.5")
        self.assertEqual(trim_decimal(Decimal("7.25")), "7.25")

    def test_blank_input(self):
        self.assertEqual(trim_decimal(None), "0")


class AmountInWordsTests(TestCase):
    def test_zero(self):
        self.assertEqual(amount_in_words(0), "Rupees Zero Only")

    def test_small_numbers(self):
        self.assertEqual(amount_in_words(7), "Rupees Seven Only")
        self.assertEqual(amount_in_words(15), "Rupees Fifteen Only")
        self.assertEqual(amount_in_words(42), "Rupees Forty Two Only")
        self.assertEqual(amount_in_words(100), "Rupees One Hundred Only")

    def test_hundreds_and_thousands(self):
        self.assertEqual(amount_in_words(3010), "Rupees Three Thousand Ten Only")
        self.assertEqual(
            amount_in_words(19999),
            "Rupees Nineteen Thousand Nine Hundred Ninety Nine Only",
        )

    def test_lakhs_and_crores_use_the_indian_system(self):
        self.assertEqual(amount_in_words(100000), "Rupees One Lakh Only")
        self.assertEqual(
            amount_in_words(1234567),
            "Rupees Twelve Lakh Thirty Four Thousand Five Hundred Sixty Seven Only",
        )
        self.assertEqual(amount_in_words(10000000), "Rupees One Crore Only")

    def test_paise_are_spelled_out(self):
        self.assertEqual(amount_in_words("10.50"), "Rupees Ten and Fifty Paise Only")
        self.assertEqual(amount_in_words("0.75"), "Rupees Zero and Seventy Five Paise Only")

    def test_negative_amounts(self):
        self.assertTrue(amount_in_words(-100).startswith("Minus Rupees"))


class TemplateFilterTests(TestCase):
    def render(self, template, **context):
        return Template("{% load billing_extras %}" + template).render(Context(context))

    def test_inr_filter(self):
        self.assertEqual(self.render("{{ v|inr }}", v=Decimal("1234567.5")), "12,34,567.50")

    def test_money_filter_adds_the_symbol(self):
        self.assertEqual(self.render("{{ v|money }}", v=1500), "Rs. 1,500.00")

    def test_in_words_filter(self):
        self.assertIn("Three Thousand Ten", self.render("{{ v|in_words }}", v=3010))

    def test_status_badge_renders_the_label(self):
        make_shop()
        invoice = make_invoice(items=[{"description": "A", "rate": "100"}])
        html = self.render("{% status_badge invoice %}", invoice=invoice)
        self.assertIn("badge", html)
        self.assertIn("Fully paid", html)

    def test_status_badge_flags_overdue_bills(self):
        from datetime import timedelta

        from django.utils import timezone

        from billing.models import PaymentStatus

        make_shop()
        invoice = make_invoice(
            items=[{"description": "A", "rate": "100"}],
            payment_status=PaymentStatus.PENDING,
            due_date=timezone.localdate() - timedelta(days=1),
        )
        html = self.render("{% status_badge invoice %}", invoice=invoice)
        self.assertIn("overdue", html)
        self.assertIn("badge-danger", html)


class ContextBuilderTests(TestCase):
    def setUp(self):
        make_shop()

    def test_context_exposes_every_documented_placeholder(self):
        from billing.services.context import PLACEHOLDER_GROUPS, build_invoice_context

        invoice = make_invoice(items=[{"description": "A", "rate": "100"}])
        context = build_invoice_context(invoice)

        for group, names in PLACEHOLDER_GROUPS.items():
            for name in names:
                root = name.split(".")[0]
                if root == "item":
                    continue  # exercised through `items` below
                self.assertIn(root, context, f"{group}: {name}")

    def test_item_dicts_carry_every_documented_key(self):
        from billing.services.context import build_invoice_context

        invoice = make_invoice(items=[{"description": "A", "rate": "100"}])
        item = build_invoice_context(invoice)["items"][0]
        for key in ["sr", "description", "qty", "rate", "discount_label", "amount", "rate_block"]:
            self.assertIn(key, item)

    def test_money_values_are_preformatted_strings(self):
        from billing.services.context import build_invoice_context

        invoice = make_invoice(items=[{"description": "A", "quantity": 2, "rate": "75000"}])
        context = build_invoice_context(invoice)
        self.assertEqual(context["grand_total"], "1,50,000.00")
        self.assertIsInstance(context["subtotal"], str)

    def test_rate_block_stacks_the_discount_under_the_rate(self):
        from billing.models import DiscountType
        from billing.services.context import build_invoice_context

        invoice = make_invoice(
            items=[{"description": "A", "rate": "1000",
                    "discount_type": DiscountType.AMOUNT, "discount_value": "100"}]
        )
        block = build_invoice_context(invoice)["items"][0]["rate_block"]
        self.assertEqual(block, "1,000.00\n-100.00")

    def test_rate_block_has_no_discount_line_when_there_is_none(self):
        from billing.services.context import build_invoice_context

        invoice = make_invoice(items=[{"description": "A", "rate": "1000"}])
        block = build_invoice_context(invoice)["items"][0]["rate_block"]
        self.assertEqual(block, "1,000.00")
        self.assertNotIn("\n", block)

    def test_dates_are_present_and_formatted(self):
        from billing.services.context import build_invoice_context

        invoice = make_invoice(items=[{"description": "A", "rate": "100"}])
        context = build_invoice_context(invoice)
        self.assertEqual(context["invoice_date"], invoice.invoice_date.strftime("%d-%m-%Y"))
        self.assertTrue(context["created_on"])
        self.assertTrue(context["created_at"])
