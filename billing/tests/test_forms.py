"""Form tests: every validation rule the shop asked for, checked server-side."""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from billing.forms import (
    InvoiceFilterForm,
    InvoiceForm,
    InvoiceLineItemForm,
    InvoiceLineItemFormSet,
)
from billing.models import Customer, DiscountType, PaymentStatus

from .factories import add_line, invoice_post_data, make_customer


def build_form(total=Decimal("1000.00"), **overrides):
    """InvoiceForm with the bill total injected, as the view does."""
    data = {
        "invoice_date": timezone.localdate().isoformat(),
        "customer_name": "Ramesh Patel",
        "customer_phone": "9876543210",
        "customer_email": "",
        "customer_city": "",
        "payment_status": PaymentStatus.PAID,
        "paid_amount": "",
        "due_date": "",
        "notes": "",
    }
    data.update({k: str(v) for k, v in overrides.items()})
    form = InvoiceForm(data)
    form.computed_total = total
    return form


class CustomerFieldTests(TestCase):
    def test_name_and_phone_are_mandatory(self):
        form = build_form(customer_name="", customer_phone="")
        self.assertFalse(form.is_valid())
        self.assertIn("customer_name", form.errors)
        self.assertIn("customer_phone", form.errors)

    def test_email_is_optional(self):
        form = build_form()
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["customer_email"], "")

    def test_invalid_email_is_rejected(self):
        form = build_form(customer_email="not-an-email")
        self.assertFalse(form.is_valid())
        self.assertIn("customer_email", form.errors)

    def test_blank_city_defaults_to_himmatnagar(self):
        form = build_form(customer_city="")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["customer_city"], "Himmatnagar")

    def test_typed_city_overrides_the_default(self):
        form = build_form(customer_city="  Ahmedabad ")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["customer_city"], "Ahmedabad")

    def test_name_whitespace_is_collapsed(self):
        form = build_form(customer_name="  Ramesh    Kumar  Patel ")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["customer_name"], "Ramesh Kumar Patel")

    def test_single_character_name_is_rejected(self):
        form = build_form(customer_name="R")
        self.assertFalse(form.is_valid())
        self.assertIn("customer_name", form.errors)

    def test_short_phone_is_rejected(self):
        form = build_form(customer_phone="12345")
        self.assertFalse(form.is_valid())
        self.assertIn("customer_phone", form.errors)

    def test_phone_formatting_characters_are_stripped(self):
        form = build_form(customer_phone="98765 43210")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["customer_phone"], "9876543210")

    def test_future_bill_date_is_rejected(self):
        tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
        form = build_form(invoice_date=tomorrow)
        self.assertFalse(form.is_valid())
        self.assertIn("invoice_date", form.errors)

    def test_bill_date_defaults_to_today(self):
        form = InvoiceForm()
        self.assertEqual(form.fields["invoice_date"].initial, timezone.localdate())


class PaymentStatusValidationTests(TestCase):
    """Exactly one status, and the amounts must agree with it."""

    def test_paid_forces_the_paid_amount_to_the_total(self):
        form = build_form(total=Decimal("1500.00"), payment_status=PaymentStatus.PAID, paid_amount="10")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["paid_amount"], Decimal("1500.00"))
        self.assertIsNone(form.cleaned_data["due_date"])

    def test_pending_requires_a_due_date(self):
        form = build_form(payment_status=PaymentStatus.PENDING, due_date="")
        self.assertFalse(form.is_valid())
        self.assertIn("due_date", form.errors)

    def test_pending_rejects_a_received_amount(self):
        due = (timezone.localdate() + timedelta(days=5)).isoformat()
        form = build_form(payment_status=PaymentStatus.PENDING, paid_amount="200", due_date=due)
        self.assertFalse(form.is_valid())
        self.assertIn("paid_amount", form.errors)

    def test_pending_is_valid_with_a_due_date_and_nothing_paid(self):
        due = (timezone.localdate() + timedelta(days=5)).isoformat()
        form = build_form(payment_status=PaymentStatus.PENDING, due_date=due)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["paid_amount"], Decimal("0.00"))

    def test_underpaid_requires_a_due_date(self):
        form = build_form(payment_status=PaymentStatus.UNDERPAID, paid_amount="400")
        self.assertFalse(form.is_valid())
        self.assertIn("due_date", form.errors)

    def test_underpaid_requires_an_amount_below_the_total(self):
        due = (timezone.localdate() + timedelta(days=5)).isoformat()
        form = build_form(
            total=Decimal("1000.00"),
            payment_status=PaymentStatus.UNDERPAID,
            paid_amount="1000",
            due_date=due,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("paid_amount", form.errors)

    def test_underpaid_rejects_zero_received(self):
        due = (timezone.localdate() + timedelta(days=5)).isoformat()
        form = build_form(payment_status=PaymentStatus.UNDERPAID, paid_amount="0", due_date=due)
        self.assertFalse(form.is_valid())
        self.assertIn("paid_amount", form.errors)

    def test_underpaid_happy_path(self):
        due = (timezone.localdate() + timedelta(days=5)).isoformat()
        form = build_form(
            total=Decimal("1000.00"),
            payment_status=PaymentStatus.UNDERPAID,
            paid_amount="400",
            due_date=due,
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_overpaid_requires_more_than_the_total(self):
        form = build_form(
            total=Decimal("1000.00"), payment_status=PaymentStatus.OVERPAID, paid_amount="1000"
        )
        self.assertFalse(form.is_valid())
        self.assertIn("paid_amount", form.errors)

    def test_overpaid_clears_the_due_date(self):
        due = (timezone.localdate() + timedelta(days=5)).isoformat()
        form = build_form(
            total=Decimal("1000.00"),
            payment_status=PaymentStatus.OVERPAID,
            paid_amount="1200",
            due_date=due,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data["due_date"])

    def test_due_date_cannot_precede_the_bill_date(self):
        yesterday = (timezone.localdate() - timedelta(days=1)).isoformat()
        form = build_form(payment_status=PaymentStatus.PENDING, due_date=yesterday)
        self.assertFalse(form.is_valid())
        self.assertIn("due_date", form.errors)

    def test_only_one_status_can_be_submitted(self):
        # A radio group posts a single value; anything outside the four choices
        # must be rejected rather than silently accepted.
        form = build_form(payment_status="SOMETHING_ELSE")
        self.assertFalse(form.is_valid())
        self.assertIn("payment_status", form.errors)


class LineItemFormTests(TestCase):
    def _form(self, **fields):
        data = {
            "description": "Cotton shirt",
            "quantity": "1",
            "unit": "",
            "rate": "1000.00",
            "discount_type": DiscountType.PERCENT,
            "discount_value": "",
        }
        data.update({k: str(v) for k, v in fields.items()})
        return InvoiceLineItemForm(data)

    def test_valid_minimum_row(self):
        form = self._form()
        self.assertTrue(form.is_valid(), form.errors)

    def test_rate_is_mandatory(self):
        form = self._form(rate="")
        self.assertFalse(form.is_valid())
        self.assertIn("rate", form.errors)

    def test_zero_rate_is_rejected(self):
        form = self._form(rate="0")
        self.assertFalse(form.is_valid())
        self.assertIn("rate", form.errors)

    def test_negative_rate_is_rejected(self):
        form = self._form(rate="-50")
        self.assertFalse(form.is_valid())
        self.assertIn("rate", form.errors)

    def test_description_is_mandatory(self):
        form = self._form(description="")
        self.assertFalse(form.is_valid())
        self.assertIn("description", form.errors)

    def test_blank_discount_becomes_zero(self):
        form = self._form(discount_value="")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["discount_value"], Decimal("0.00"))

    def test_explicit_zero_discount_is_accepted_and_normalised(self):
        form = self._form(discount_value="0")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["discount_value"], Decimal("0.00"))

    def test_negative_discount_is_rejected(self):
        form = self._form(discount_value="-10")
        self.assertFalse(form.is_valid())
        self.assertIn("discount_value", form.errors)

    def test_percent_discount_over_100_is_rejected(self):
        form = self._form(discount_type=DiscountType.PERCENT, discount_value="101")
        self.assertFalse(form.is_valid())
        self.assertIn("discount_value", form.errors)

    def test_percent_discount_of_exactly_100_is_allowed(self):
        form = self._form(discount_type=DiscountType.PERCENT, discount_value="100")
        self.assertTrue(form.is_valid(), form.errors)

    def test_amount_discount_above_the_line_value_is_rejected(self):
        form = self._form(rate="500", quantity="2", discount_type=DiscountType.AMOUNT,
                          discount_value="1500")
        self.assertFalse(form.is_valid())
        self.assertIn("discount_value", form.errors)

    def test_amount_discount_equal_to_the_line_value_is_allowed(self):
        form = self._form(rate="500", quantity="2", discount_type=DiscountType.AMOUNT,
                          discount_value="1000")
        self.assertTrue(form.is_valid(), form.errors)

    def test_zero_quantity_is_rejected(self):
        form = self._form(quantity="0")
        self.assertFalse(form.is_valid())
        self.assertIn("quantity", form.errors)

    def test_fractional_quantity_is_allowed(self):
        form = self._form(quantity="2.5", rate="40")
        self.assertTrue(form.is_valid(), form.errors)


class LineItemFormSetTests(TestCase):
    def test_at_least_one_item_is_required(self):
        data = invoice_post_data()
        data["items-0-description"] = ""
        data["items-0-rate"] = ""
        formset = InvoiceLineItemFormSet(data)
        self.assertFalse(formset.is_valid())

    def test_totals_are_computed_from_the_rows(self):
        data = invoice_post_data(**{"items-0-quantity": "2", "items-0-rate": "750",
                                    "items-0-discount_value": "10"})
        add_line(data, 1, rate="1450", discount_type=DiscountType.AMOUNT, discount_value="150")
        add_line(data, 2, rate="120", quantity="3")

        formset = InvoiceLineItemFormSet(data)
        self.assertTrue(formset.is_valid(), formset.errors)

        totals = formset.computed_totals()
        self.assertEqual(totals["subtotal"], Decimal("3310.00"))
        self.assertEqual(totals["total_discount"], Decimal("300.00"))
        self.assertEqual(totals["grand_total"], Decimal("3010.00"))

    def test_one_bad_row_invalidates_the_whole_bill(self):
        data = invoice_post_data()
        add_line(data, 1, rate="")  # missing mandatory rate
        formset = InvoiceLineItemFormSet(data)
        self.assertFalse(formset.is_valid())

    def test_many_rows_are_accepted(self):
        data = invoice_post_data()
        for index in range(1, 12):
            add_line(data, index, rate="10")
        formset = InvoiceLineItemFormSet(data)
        self.assertTrue(formset.is_valid(), formset.errors)
        self.assertEqual(formset.computed_totals()["grand_total"], Decimal("1110.00"))


class CustomerSyncTests(TestCase):
    def test_new_customer_is_created(self):
        form = build_form()
        self.assertTrue(form.is_valid(), form.errors)
        customer = form.sync_customer()
        self.assertEqual(Customer.objects.count(), 1)
        self.assertEqual(customer.city, "Himmatnagar")

    def test_existing_customer_is_reused_by_phone(self):
        existing = make_customer(phone="9876543210", name="Old Name")
        form = build_form(customer_name="New Name")
        self.assertTrue(form.is_valid(), form.errors)
        customer = form.sync_customer()
        self.assertEqual(customer.pk, existing.pk)
        self.assertEqual(Customer.objects.count(), 1)
        self.assertEqual(customer.name, "New Name")

    def test_blank_email_does_not_erase_a_stored_one(self):
        make_customer(phone="9876543210", email="known@example.test")
        form = build_form(customer_email="")
        self.assertTrue(form.is_valid(), form.errors)
        customer = form.sync_customer()
        self.assertEqual(customer.email, "known@example.test")


class FilterFormTests(TestCase):
    def test_all_fields_are_optional(self):
        self.assertTrue(InvoiceFilterForm({}).is_valid())

    def test_reversed_date_range_is_rejected(self):
        form = InvoiceFilterForm(
            {"date_from": "2026-05-10", "date_to": "2026-05-01"}
        )
        self.assertFalse(form.is_valid())
        self.assertIn("date_to", form.errors)

    def test_reversed_amount_range_is_rejected(self):
        form = InvoiceFilterForm({"min_amount": "500", "max_amount": "100"})
        self.assertFalse(form.is_valid())
        self.assertIn("max_amount", form.errors)

    def test_created_on_filter_exists_and_parses(self):
        form = InvoiceFilterForm({"created_on": timezone.localdate().isoformat()})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["created_on"], timezone.localdate())

    def test_payment_choices_include_the_outstanding_shortcut(self):
        values = [choice[0] for choice in InvoiceFilterForm.PAYMENT_CHOICES]
        for expected in ["", "OUTSTANDING", "PAID", "OVERPAID", "UNDERPAID", "PENDING", "OVERDUE"]:
            self.assertIn(expected, values)
