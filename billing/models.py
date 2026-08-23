"""
Billing domain models.

Money rules that the whole app relies on:

* Every money value is a Decimal with 2 decimal places, quantised with
  ROUND_HALF_UP (what a shopkeeper expects, unlike Python's default
  banker's rounding).
* Line totals, invoice totals and the balance are *stored*, not computed on
  read. A printed bill must never change its numbers later because a
  calculation was tweaked.
* A discount of zero is treated as "no discount" and is never rendered.
"""
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models, transaction
from django.urls import reverse
from django.utils import timezone

TWO_PLACES = Decimal("0.01")
ZERO = Decimal("0.00")

phone_validator = RegexValidator(
    regex=r"^[0-9+\-\s()]{7,20}$",
    message="Enter a valid phone number (digits, spaces, + - ( ) allowed).",
)


def money(value) -> Decimal:
    """Coerce anything numeric to a 2-decimal Decimal, rounding half up."""
    if value in (None, ""):
        return ZERO
    return Decimal(str(value)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Shop identity (printed on every bill)
# ---------------------------------------------------------------------------
class ShopProfile(models.Model):
    """
    Singleton row holding the shop letterhead.

    Editable from the Django admin so the bill format can be adjusted without
    touching code.
    """

    name = models.CharField(max_length=150, default="My Shop")
    tagline = models.CharField(max_length=200, blank=True)
    address_line1 = models.CharField(max_length=200, blank=True)
    address_line2 = models.CharField(max_length=200, blank=True)
    city = models.CharField(max_length=100, default="Himmatnagar")
    state = models.CharField(max_length=100, blank=True, default="Gujarat")
    pincode = models.CharField(max_length=12, blank=True)
    phone = models.CharField(max_length=40, blank=True)
    alt_phone = models.CharField(max_length=40, blank=True)
    email = models.EmailField(blank=True)
    gstin = models.CharField("GSTIN", max_length=20, blank=True)
    logo = models.ImageField(upload_to="shop/", blank=True, null=True)

    invoice_footer_note = models.TextField(
        blank=True,
        default="Thank you for your business!",
        help_text="Printed at the bottom of every bill.",
    )
    terms = models.TextField(
        blank=True,
        help_text="Optional terms and conditions block on the bill.",
    )
    show_signature_line = models.BooleanField(default=True)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Shop profile"
        verbose_name_plural = "Shop profile"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.pk = 1  # enforce singleton
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> "ShopProfile":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def address_block(self) -> list[str]:
        city_line = ", ".join(p for p in [self.city, self.state] if p)
        if self.pincode:
            city_line = f"{city_line} - {self.pincode}".strip(" -")
        return [
            line
            for line in (self.address_line1, self.address_line2, city_line)
            if line
        ]


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------
class Customer(models.Model):
    """
    Reusable customer record, matched on phone number.

    Invoices also keep a *snapshot* of these fields so that editing a customer
    never rewrites history on bills already handed to them.
    """

    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=20, validators=[phone_validator], db_index=True)
    email = models.EmailField(blank=True)
    city = models.CharField(max_length=100, blank=True)
    address = models.TextField(blank=True)
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(fields=["phone"], name="uniq_customer_phone"),
        ]
        indexes = [
            models.Index(fields=["name"]),
            models.Index(fields=["city"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.phone})"

    def save(self, *args, **kwargs):
        self.city = (self.city or settings.DEFAULT_CITY).strip()
        self.phone = self.phone.strip()
        self.email = (self.email or "").strip().lower()
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# Invoice numbering
# ---------------------------------------------------------------------------
class InvoiceSequence(models.Model):
    """
    One counter row per (prefix, financial year).

    Numbers are handed out inside a transaction with SELECT ... FOR UPDATE, so
    two simultaneous saves can never produce the same bill number.
    """

    prefix = models.CharField(max_length=16)
    period = models.CharField(max_length=16, help_text="Financial year, e.g. 2026-27")
    last_number = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["prefix", "period"], name="uniq_sequence_period"),
        ]

    def __str__(self):
        return f"{self.prefix}/{self.period} -> {self.last_number}"

    @staticmethod
    def financial_year(on_date=None) -> str:
        """Indian FY: April 1 to March 31. 2026-05-01 -> '2026-27'."""
        d = on_date or timezone.localdate()
        start = d.year if d.month >= 4 else d.year - 1
        return f"{start}-{str(start + 1)[-2:]}"

    @classmethod
    def next_number(cls, on_date=None, prefix: str | None = None) -> str:
        prefix = prefix or settings.INVOICE_NUMBER_PREFIX
        period = cls.financial_year(on_date)
        with transaction.atomic():
            seq, _ = cls.objects.select_for_update().get_or_create(
                prefix=prefix, period=period
            )
            seq.last_number += 1
            seq.save(update_fields=["last_number"])
            return f"{prefix}/{period}/{seq.last_number:04d}"


# ---------------------------------------------------------------------------
# Invoice
# ---------------------------------------------------------------------------
class PaymentStatus(models.TextChoices):
    PAID = "PAID", "Fully paid"
    OVERPAID = "OVERPAID", "Overpaid"
    UNDERPAID = "UNDERPAID", "Partially paid (underpaid)"
    PENDING = "PENDING", "Pending (nothing paid)"


class DiscountType(models.TextChoices):
    PERCENT = "PERCENT", "% (percentage)"
    AMOUNT = "AMOUNT", "Amount"


class InvoiceQuerySet(models.QuerySet):
    def outstanding(self):
        """Bills where the shop is still owed money."""
        return self.filter(
            payment_status__in=[PaymentStatus.PENDING, PaymentStatus.UNDERPAID]
        )

    def overdue(self, on_date=None):
        return self.outstanding().filter(due_date__lt=on_date or timezone.localdate())

    def with_related(self):
        return self.select_related("customer", "created_by").prefetch_related("items")


class Invoice(models.Model):
    number = models.CharField(max_length=32, unique=True, editable=False, db_index=True)
    invoice_date = models.DateField(default=timezone.localdate, db_index=True)

    customer = models.ForeignKey(
        Customer, on_delete=models.PROTECT, related_name="invoices"
    )

    # --- snapshot of the customer at billing time --------------------------
    customer_name = models.CharField(max_length=150)
    customer_phone = models.CharField(max_length=20, db_index=True)
    customer_email = models.EmailField(blank=True)
    customer_city = models.CharField(max_length=100)

    # --- stored money ------------------------------------------------------
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    total_discount = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    grand_total = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)

    # --- payment (exactly one status) --------------------------------------
    payment_status = models.CharField(
        max_length=12, choices=PaymentStatus.choices, default=PaymentStatus.PAID
    )
    paid_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=ZERO, validators=[MinValueValidator(ZERO)]
    )
    # grand_total - paid_amount:  > 0 still owed, < 0 overpaid.
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    due_date = models.DateField(
        null=True, blank=True, help_text="Required when pending or underpaid."
    )

    notes = models.TextField(blank=True)

    # --- where the generated document lives on disk -------------------------
    # Full absolute path of the archived PDF, so the file can be reopened,
    # re-attached or emailed straight from the database record.
    pdf_path = models.CharField(
        "PDF file path",
        max_length=500,
        blank=True,
        help_text="Absolute path of the archived PDF on this computer.",
    )
    pdf_saved_at = models.DateTimeField(null=True, blank=True)

    # --- audit -------------------------------------------------------------
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="invoices_created",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    print_count = models.PositiveIntegerField(default=0)
    last_printed_at = models.DateTimeField(null=True, blank=True)

    objects = InvoiceQuerySet.as_manager()

    class Meta:
        ordering = ("-invoice_date", "-id")
        indexes = [
            models.Index(fields=["customer_name"]),
            models.Index(fields=["customer_city"]),
            models.Index(fields=["payment_status", "due_date"]),
            models.Index(fields=["-created_at"]),
        ]

    def __str__(self):
        return f"{self.number} - {self.customer_name}"

    def get_absolute_url(self):
        return reverse("billing:invoice_detail", args=[self.pk])

    # -- lifecycle ---------------------------------------------------------
    def save(self, *args, **kwargs):
        if not self.number:
            self.number = InvoiceSequence.next_number(self.invoice_date)
        if self.customer_id and not self.customer_name:
            self.customer_name = self.customer.name
            self.customer_phone = self.customer.phone
            self.customer_email = self.customer.email
            self.customer_city = self.customer.city
        self.customer_city = (self.customer_city or settings.DEFAULT_CITY).strip()
        super().save(*args, **kwargs)

    def recalculate(self, commit: bool = True):
        """Roll line items up into the invoice totals and the balance."""
        subtotal = ZERO
        discount = ZERO
        for item in self.items.all():
            subtotal += item.gross_amount
            discount += item.discount_amount
        self.subtotal = money(subtotal)
        self.total_discount = money(discount)
        self.grand_total = money(subtotal - discount)
        self.balance = money(self.grand_total - self.paid_amount)
        if commit:
            self.save(
                update_fields=[
                    "subtotal",
                    "total_discount",
                    "grand_total",
                    "balance",
                    "updated_at",
                ]
            )
        return self

    def apply_payment_status(self):
        """
        Normalise paid_amount / due_date to agree with the chosen status.

        The four statuses are mutually exclusive, so the status is the single
        source of truth and the amounts are derived from it.
        """
        if self.payment_status == PaymentStatus.PAID:
            self.paid_amount = self.grand_total
            self.due_date = None
        elif self.payment_status == PaymentStatus.PENDING:
            self.paid_amount = ZERO
            # due_date stays: it is mandatory for pending bills.
        elif self.payment_status == PaymentStatus.OVERPAID:
            self.due_date = None
        self.balance = money(self.grand_total - self.paid_amount)
        return self

    # -- derived, for templates -------------------------------------------
    @property
    def amount_pending(self) -> Decimal:
        """Money still owed to the shop (0 when paid or overpaid)."""
        return self.balance if self.balance > ZERO else ZERO

    @property
    def amount_overpaid(self) -> Decimal:
        """Money to be returned to the customer (0 otherwise)."""
        return -self.balance if self.balance < ZERO else ZERO

    @property
    def is_outstanding(self) -> bool:
        return self.payment_status in (PaymentStatus.PENDING, PaymentStatus.UNDERPAID)

    @property
    def is_overdue(self) -> bool:
        return bool(
            self.is_outstanding and self.due_date and self.due_date < timezone.localdate()
        )

    @property
    def status_css(self) -> str:
        return {
            PaymentStatus.PAID: "ok",
            PaymentStatus.OVERPAID: "info",
            PaymentStatus.UNDERPAID: "warn",
            PaymentStatus.PENDING: "danger",
        }[PaymentStatus(self.payment_status)]

    @property
    def pdf_filename(self) -> str:
        """
        `<database id>_<customer name>.pdf`, e.g. ``26_Ramesh_Patel.pdf``.

        The id keeps the name unique and sorts the archive folder in the order
        the bills were created; the name makes a file findable by eye. Anything
        Windows dislikes in a filename is stripped out.
        """
        safe_name = "".join(
            c for c in self.customer_name if c.isalnum() or c in " -_"
        ).strip().replace(" ", "_")
        return f"{self.pk or 'unsaved'}_{safe_name or 'customer'}.pdf"

    @property
    def pdf_exists(self) -> bool:
        """False when the archived file has been moved or deleted by hand."""
        from pathlib import Path

        return bool(self.pdf_path) and Path(self.pdf_path).is_file()

    @property
    def pdf_folder(self) -> str:
        from pathlib import Path

        return str(Path(self.pdf_path).parent) if self.pdf_path else ""

    def mark_printed(self):
        self.print_count = models.F("print_count") + 1
        self.last_printed_at = timezone.now()
        self.save(update_fields=["print_count", "last_printed_at"])
        self.refresh_from_db(fields=["print_count"])


class InvoiceLineItem(models.Model):
    """
    One billed line.

    `rate` is mandatory and must be positive. The discount is optional: a
    discount_value of 0 means "no discount" and is never printed.
    """

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="items")
    position = models.PositiveIntegerField(default=0)

    description = models.CharField("Item / service", max_length=255)
    hsn_code = models.CharField("Code", max_length=20, blank=True)
    quantity = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("1.00"),
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    unit = models.CharField(max_length=16, blank=True, default="")
    rate = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )

    discount_type = models.CharField(
        max_length=8, choices=DiscountType.choices, default=DiscountType.PERCENT
    )
    discount_value = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=ZERO,
        validators=[MinValueValidator(ZERO)],
        help_text="0 means no discount; it is not shown on the bill.",
    )

    # stored results, so a printed bill is immutable
    gross_amount = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    net_amount = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)

    class Meta:
        ordering = ("position", "id")

    def __str__(self):
        return f"{self.description} x {self.quantity}"

    # -- calculation -------------------------------------------------------
    def compute(self):
        gross = money(Decimal(str(self.quantity)) * Decimal(str(self.rate)))
        value = money(self.discount_value)

        if value <= ZERO:
            discount = ZERO
        elif self.discount_type == DiscountType.PERCENT:
            discount = money(gross * min(value, Decimal("100")) / Decimal("100"))
        else:
            discount = min(money(value), gross)  # never discount below zero

        self.gross_amount = gross
        self.discount_amount = discount
        self.net_amount = money(gross - discount)
        return self

    def save(self, *args, **kwargs):
        self.compute()
        super().save(*args, **kwargs)

    # -- presentation ------------------------------------------------------
    @property
    def has_discount(self) -> bool:
        return self.discount_amount > ZERO

    @property
    def discount_label(self) -> str:
        """
        What is printed under the rate.

        Percentage discount ->  "-10% (-120.00)"
        Amount discount     ->  "-120.00"
        No discount         ->  ""  (nothing is printed)
        """
        if not self.has_discount:
            return ""
        if self.discount_type == DiscountType.PERCENT:
            from billing.utils import trim_decimal

            pct = trim_decimal(money(self.discount_value))
            return f"-{pct}% (-{self.discount_amount})"
        return f"-{self.discount_amount}"


# ---------------------------------------------------------------------------
# Print audit trail
# ---------------------------------------------------------------------------
class PrintJob(models.Model):
    class Status(models.TextChoices):
        QUEUED = "QUEUED", "Queued"
        SENT = "SENT", "Sent to printer"
        FAILED = "FAILED", "Failed"
        MOCKED = "MOCKED", "Mocked (no printer)"

    invoice = models.ForeignKey(
        Invoice, on_delete=models.CASCADE, related_name="print_jobs"
    )
    backend = models.CharField(max_length=32)
    printer_name = models.CharField(max_length=200, blank=True)
    copies = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(
        max_length=8, choices=Status.choices, default=Status.QUEUED
    )
    message = models.TextField(blank=True)
    spooled_file = models.CharField(max_length=500, blank=True)

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.invoice.number} via {self.backend} [{self.status}]"

    @property
    def succeeded(self) -> bool:
        return self.status in (self.Status.SENT, self.Status.MOCKED)
