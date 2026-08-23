"""
Billing forms.

All the business rules asked for are enforced here (and mirrored in the
browser by static/js/invoice_form.js, which is convenience only - the server
is the authority):

* Customer name and phone are mandatory; email is optional.
* City defaults to Himmatnagar, and is overwritten when something is typed.
* Every line needs a description and a rate > 0.
* Discount is per line, either % or a flat amount; 0 / blank means no
  discount and it is never printed.
* A % discount cannot exceed 100, and an amount discount cannot exceed the
  line value (no negative lines).
* Exactly one of the four payment states, with the paid amount and the due
  date validated against it.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from django import forms
from django.conf import settings
from django.forms import BaseInlineFormSet, inlineformset_factory
from django.utils import timezone

from .models import (
    Customer,
    DiscountType,
    Invoice,
    InvoiceLineItem,
    PaymentStatus,
    ShopProfile,
    money,
)

ZERO = Decimal("0.00")

CTRL = "form-control"
SELECT = "form-select"


# ---------------------------------------------------------------------------
# Invoice header (customer + payment)
# ---------------------------------------------------------------------------
class InvoiceForm(forms.ModelForm):
    """Customer details, the payment state and the due date."""

    customer_name = forms.CharField(
        label="Customer name",
        max_length=150,
        widget=forms.TextInput(
            attrs={"class": CTRL, "placeholder": "Full name", "autofocus": True}
        ),
    )
    customer_phone = forms.CharField(
        label="Phone number",
        max_length=20,
        widget=forms.TextInput(
            attrs={
                "class": CTRL,
                "placeholder": "10-digit mobile number",
                "inputmode": "tel",
            }
        ),
    )
    customer_email = forms.EmailField(
        label="Email",
        required=False,
        widget=forms.EmailInput(
            attrs={"class": CTRL, "placeholder": "Optional"}
        ),
    )
    customer_city = forms.CharField(
        label="City",
        max_length=100,
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": CTRL,
                "placeholder": settings.DEFAULT_CITY,
            }
        ),
        help_text=f"Leave blank for {settings.DEFAULT_CITY}.",
    )

    # Carried through every render of the form; see Invoice.form_token.
    form_token = forms.CharField(required=False, widget=forms.HiddenInput)

    payment_status = forms.ChoiceField(
        label="Payment",
        choices=PaymentStatus.choices,
        initial=PaymentStatus.PAID,
        widget=forms.RadioSelect(attrs={"class": "pay-radio"}),
        help_text="Pick exactly one.",
    )
    paid_amount = forms.DecimalField(
        label="Amount received",
        required=False,
        min_value=ZERO,
        max_digits=12,
        decimal_places=2,
        widget=forms.NumberInput(
            attrs={"class": CTRL, "step": "0.01", "min": "0", "placeholder": "0.00"}
        ),
    )
    due_date = forms.DateField(
        label="Due date",
        required=False,
        widget=forms.DateInput(attrs={"class": CTRL, "type": "date"}),
    )

    class Meta:
        model = Invoice
        fields = (
            "invoice_date",
            "customer_name",
            "customer_phone",
            "customer_email",
            "customer_city",
            "payment_status",
            "paid_amount",
            "due_date",
            "notes",
        )
        widgets = {
            "invoice_date": forms.DateInput(attrs={"class": CTRL, "type": "date"}),
            "notes": forms.Textarea(
                attrs={"class": CTRL, "rows": 2, "placeholder": "Optional note on the bill"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            self.fields["invoice_date"].initial = timezone.localdate()
            # A new key per rendered form, so one form can save exactly one bill.
            self.fields["form_token"].initial = uuid4().hex
        # `grand_total` is injected by the view from the validated formset, so
        # the payment checks below can compare against the real bill amount.
        self.computed_total: Decimal = ZERO

    # -- field level -------------------------------------------------------
    def clean_customer_name(self):
        name = " ".join(self.cleaned_data["customer_name"].split())
        if len(name) < 2:
            raise forms.ValidationError("Please enter the customer's full name.")
        return name

    def clean_customer_phone(self):
        raw = self.cleaned_data["customer_phone"]
        digits = "".join(ch for ch in raw if ch.isdigit())
        if len(digits) < 7:
            raise forms.ValidationError("Enter a valid phone number (at least 7 digits).")
        if len(digits) > 15:
            raise forms.ValidationError("That phone number is too long.")
        return digits if len(digits) == len(raw.replace(" ", "")) else raw.strip()

    def clean_customer_city(self):
        # Blank -> the shop's home city; anything typed overwrites it.
        return (self.cleaned_data.get("customer_city") or "").strip() or settings.DEFAULT_CITY

    def clean_invoice_date(self):
        value = self.cleaned_data["invoice_date"]
        if value > timezone.localdate():
            raise forms.ValidationError("The bill date cannot be in the future.")
        return value

    # -- cross field -------------------------------------------------------
    def clean(self):
        cleaned = super().clean()
        status = cleaned.get("payment_status")
        paid = money(cleaned.get("paid_amount") or ZERO)
        due = cleaned.get("due_date")
        total = money(self.computed_total)

        if not status:
            return cleaned

        if total <= ZERO:
            # The formset reports the real error; skip payment checks so the
            # user is not buried in messages.
            return cleaned

        if status == PaymentStatus.PAID:
            cleaned["paid_amount"] = total
            cleaned["due_date"] = None

        elif status == PaymentStatus.PENDING:
            if paid > ZERO:
                self.add_error(
                    "paid_amount",
                    "For a pending bill the received amount must be 0. "
                    "Choose 'Partially paid' if something was received.",
                )
            cleaned["paid_amount"] = ZERO
            if not due:
                self.add_error("due_date", "A due date is required for a pending bill.")

        elif status == PaymentStatus.UNDERPAID:
            if paid <= ZERO:
                self.add_error(
                    "paid_amount",
                    "Enter how much was received, or choose 'Pending' instead.",
                )
            elif paid >= total:
                self.add_error(
                    "paid_amount",
                    f"{paid} is not less than the bill total {total}. "
                    f"Choose 'Fully paid' or 'Overpaid' instead.",
                )
            if not due:
                self.add_error("due_date", "A due date is required when the bill is underpaid.")

        elif status == PaymentStatus.OVERPAID:
            if paid <= total:
                self.add_error(
                    "paid_amount",
                    f"An overpaid bill needs more than the total {total}.",
                )
            cleaned["due_date"] = None

        if cleaned.get("due_date") and cleaned["due_date"] < cleaned.get(
            "invoice_date", timezone.localdate()
        ):
            self.add_error("due_date", "The due date cannot be before the bill date.")

        return cleaned

    # -- persistence -------------------------------------------------------
    def sync_customer(self) -> Customer:
        """
        Find-or-create the customer record, matched on phone number.

        Keeps the customer list clean for the filters on the listing page while
        still letting the same person be billed repeatedly.
        """
        data = self.cleaned_data
        customer, created = Customer.objects.get_or_create(
            phone=data["customer_phone"],
            defaults={
                "name": data["customer_name"],
                "email": data.get("customer_email", ""),
                "city": data["customer_city"],
            },
        )
        if not created:
            # Latest bill wins for the master record; the invoice keeps its own
            # snapshot, so older bills are unaffected.
            customer.name = data["customer_name"]
            customer.city = data["customer_city"]
            if data.get("customer_email"):
                customer.email = data["customer_email"]
            customer.save(update_fields=["name", "city", "email", "updated_at"])
        return customer


# ---------------------------------------------------------------------------
# Line items
# ---------------------------------------------------------------------------
class InvoiceLineItemForm(forms.ModelForm):
    class Meta:
        model = InvoiceLineItem
        fields = (
            "description",
            "quantity",
            "unit",
            "rate",
            "discount_type",
            "discount_value",
        )
        widgets = {
            "description": forms.TextInput(
                attrs={"class": CTRL + " li-desc", "placeholder": "Item or service"}
            ),
            "quantity": forms.NumberInput(
                attrs={
                    "class": CTRL + " li-qty",
                    "step": "0.01",
                    "min": "0.01",
                    "value": "1",
                }
            ),
            "unit": forms.TextInput(
                attrs={"class": CTRL + " li-unit", "placeholder": "pcs", "maxlength": 16}
            ),
            "rate": forms.NumberInput(
                attrs={
                    "class": CTRL + " li-rate",
                    "step": "0.01",
                    "min": "0.01",
                    "placeholder": "0.00",
                }
            ),
            "discount_type": forms.Select(attrs={"class": SELECT + " li-disc-type"}),
            "discount_value": forms.NumberInput(
                attrs={
                    "class": CTRL + " li-disc-value",
                    "step": "0.01",
                    "min": "0",
                    "placeholder": "0",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["quantity"].initial = Decimal("1")
        self.fields["discount_value"].required = False
        self.fields["unit"].required = False
        self.fields["rate"].required = True  # rate is mandatory

    def clean_description(self):
        text = " ".join(self.cleaned_data["description"].split())
        if not text:
            raise forms.ValidationError("Describe the item.")
        return text

    def clean_rate(self):
        rate = self.cleaned_data.get("rate")
        if rate is None:
            raise forms.ValidationError("Rate is required.")
        if rate <= ZERO:
            raise forms.ValidationError("Rate must be more than 0.")
        return money(rate)

    def clean_quantity(self):
        qty = self.cleaned_data.get("quantity")
        if qty is None:
            return Decimal("1")  # blank means one unit
        if qty <= ZERO:
            raise forms.ValidationError("Quantity must be more than 0.")
        return qty

    def clean_discount_value(self):
        raw = self.cleaned_data.get("discount_value")
        if raw in (None, ""):
            return ZERO
        try:
            value = money(raw)
        except (InvalidOperation, TypeError):
            raise forms.ValidationError("Enter a number.") from None
        if value < ZERO:
            raise forms.ValidationError("A discount cannot be negative.")
        return value

    def clean(self):
        cleaned = super().clean()
        discount = cleaned.get("discount_value") or ZERO
        dtype = cleaned.get("discount_type")
        rate = cleaned.get("rate") or ZERO
        qty = cleaned.get("quantity")
        qty = Decimal("1") if qty is None else qty

        # 0 or blank simply means "no discount"; it is never shown on the bill.
        if discount <= ZERO:
            cleaned["discount_value"] = ZERO
            return cleaned

        if dtype == DiscountType.PERCENT and discount > Decimal("100"):
            self.add_error("discount_value", "A percentage discount cannot exceed 100%.")
        elif dtype == DiscountType.AMOUNT:
            line_total = money(rate * qty)
            if discount > line_total:
                self.add_error(
                    "discount_value",
                    f"The discount cannot exceed the line value of {line_total}.",
                )
        return cleaned


class BaseInvoiceLineItemFormSet(BaseInlineFormSet):
    """Requires at least one real line, and keeps the row order stable."""

    def clean(self):
        super().clean()
        if any(self.errors):
            return

        live_forms = [
            f
            for f in self.forms
            if f.cleaned_data
            and not f.cleaned_data.get("DELETE")
            and f.cleaned_data.get("description")
        ]
        if not live_forms:
            raise forms.ValidationError("Add at least one item to the bill.")
        if len(live_forms) > settings.MAX_LINE_ITEMS:
            raise forms.ValidationError(
                f"A single bill can hold at most {settings.MAX_LINE_ITEMS} items."
            )
        self.live_forms = live_forms

    def computed_totals(self) -> dict[str, Decimal]:
        """Totals from the validated rows, used by InvoiceForm's payment checks."""
        gross = ZERO
        discount = ZERO
        for form in getattr(self, "live_forms", []):
            item = InvoiceLineItem(
                quantity=form.cleaned_data.get("quantity") or Decimal("1"),  # noqa: E501
                rate=form.cleaned_data.get("rate") or ZERO,
                discount_type=form.cleaned_data.get("discount_type"),
                discount_value=form.cleaned_data.get("discount_value") or ZERO,
            ).compute()
            gross += item.gross_amount
            discount += item.discount_amount
        return {
            "subtotal": money(gross),
            "total_discount": money(discount),
            "grand_total": money(gross - discount),
        }


InvoiceLineItemFormSet = inlineformset_factory(
    Invoice,
    InvoiceLineItem,
    form=InvoiceLineItemForm,
    formset=BaseInvoiceLineItemFormSet,
    extra=1,
    min_num=1,
    validate_min=True,
    can_delete=True,
)


# ---------------------------------------------------------------------------
# Listing page filters
# ---------------------------------------------------------------------------
class InvoiceFilterForm(forms.Form):
    """Filters on the bill listing. Every field is optional."""

    OUTSTANDING = "OUTSTANDING"
    PAYMENT_CHOICES = [
        ("", "Any payment status"),
        (OUTSTANDING, "Pending or underpaid (money owed)"),
        *PaymentStatus.choices,
        ("OVERDUE", "Overdue (due date passed)"),
    ]

    q = forms.CharField(
        label="Search",
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": CTRL,
                "placeholder": "Bill no, name, phone, email or city",
                "autocomplete": "off",
            }
        ),
    )
    name = forms.CharField(
        label="Customer name",
        required=False,
        widget=forms.TextInput(attrs={"class": CTRL, "placeholder": "Name contains"}),
    )
    phone = forms.CharField(
        label="Mobile number",
        required=False,
        widget=forms.TextInput(attrs={"class": CTRL, "placeholder": "Phone contains"}),
    )
    email = forms.CharField(
        label="Email",
        required=False,
        widget=forms.TextInput(attrs={"class": CTRL, "placeholder": "Email contains"}),
    )
    city = forms.CharField(
        label="City",
        required=False,
        widget=forms.TextInput(attrs={"class": CTRL, "placeholder": "City"}),
    )
    payment = forms.ChoiceField(
        label="Payment",
        required=False,
        choices=PAYMENT_CHOICES,
        widget=forms.Select(attrs={"class": SELECT}),
    )
    date_from = forms.DateField(
        label="Bill date from",
        required=False,
        widget=forms.DateInput(attrs={"class": CTRL, "type": "date"}),
    )
    date_to = forms.DateField(
        label="Bill date to",
        required=False,
        widget=forms.DateInput(attrs={"class": CTRL, "type": "date"}),
    )
    created_on = forms.DateField(
        label="Created on",
        required=False,
        widget=forms.DateInput(attrs={"class": CTRL, "type": "date"}),
        help_text="Exact day the bill was entered in the system.",
    )
    min_amount = forms.DecimalField(
        label="Min total",
        required=False,
        widget=forms.NumberInput(attrs={"class": CTRL, "step": "0.01", "placeholder": "0"}),
    )
    max_amount = forms.DecimalField(
        label="Max total",
        required=False,
        widget=forms.NumberInput(attrs={"class": CTRL, "step": "0.01", "placeholder": "Any"}),
    )

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("date_from"), cleaned.get("date_to")
        if start and end and start > end:
            self.add_error("date_to", "The 'to' date must not be before the 'from' date.")
        low, high = cleaned.get("min_amount"), cleaned.get("max_amount")
        if low is not None and high is not None and low > high:
            self.add_error("max_amount", "The maximum must not be below the minimum.")
        return cleaned

    @property
    def has_filters(self) -> bool:
        return any(
            self.cleaned_data.get(name) not in (None, "", [])
            for name in self.fields
            if hasattr(self, "cleaned_data")
        )


# ---------------------------------------------------------------------------
# Shop profile (bill letterhead)
# ---------------------------------------------------------------------------
class ShopProfileForm(forms.ModelForm):
    class Meta:
        model = ShopProfile
        exclude = ("updated_at",)
        widgets = {
            "invoice_footer_note": forms.Textarea(attrs={"rows": 2}),
            "terms": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault("class", "form-check-input")
            elif isinstance(field.widget, forms.ClearableFileInput):
                field.widget.attrs.setdefault("class", CTRL)
            else:
                field.widget.attrs.setdefault("class", CTRL)


# ---------------------------------------------------------------------------
# Quick "record a payment" form on the detail page
# ---------------------------------------------------------------------------
class RecordPaymentForm(forms.Form):
    """Settle or part-settle an outstanding bill without editing every line."""

    amount = forms.DecimalField(
        label="Amount received now",
        min_value=Decimal("0.01"),
        max_digits=12,
        decimal_places=2,
        widget=forms.NumberInput(attrs={"class": CTRL, "step": "0.01"}),
    )
    new_due_date = forms.DateField(
        label="Revised due date",
        required=False,
        widget=forms.DateInput(attrs={"class": CTRL, "type": "date"}),
    )

    def __init__(self, *args, invoice: Invoice | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.invoice = invoice

    def clean(self):
        cleaned = super().clean()
        if not self.invoice:
            return cleaned
        amount = cleaned.get("amount") or ZERO
        remaining = self.invoice.amount_pending
        if remaining <= ZERO:
            raise forms.ValidationError("This bill has nothing pending.")
        new_total_paid = money(self.invoice.paid_amount + amount)
        if new_total_paid < self.invoice.grand_total and not cleaned.get("new_due_date"):
            cleaned["new_due_date"] = self.invoice.due_date or date.today()
        return cleaned
