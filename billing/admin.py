"""Django admin: the back office for the shop owner."""
from django.contrib import admin
from django.utils.html import format_html

from .models import (
    Customer,
    Invoice,
    InvoiceLineItem,
    InvoiceSequence,
    PrintJob,
    ShopProfile,
)


@admin.register(ShopProfile)
class ShopProfileAdmin(admin.ModelAdmin):
    """Singleton: the letterhead printed on every bill."""

    fieldsets = (
        ("Identity", {"fields": ("name", "tagline", "logo", "gstin")}),
        (
            "Address",
            {"fields": ("address_line1", "address_line2", "city", "state", "pincode")},
        ),
        ("Contact", {"fields": ("phone", "alt_phone", "email")}),
        ("Bill footer", {"fields": ("invoice_footer_note", "terms", "show_signature_line")}),
    )

    def has_add_permission(self, request):
        return not ShopProfile.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "email", "city", "bill_count", "updated_at")
    search_fields = ("name", "phone", "email", "city")
    list_filter = ("city",)
    readonly_fields = ("created_at", "updated_at")

    @admin.display(description="Bills")
    def bill_count(self, obj):
        return obj.invoices.count()


class InvoiceLineItemInline(admin.TabularInline):
    model = InvoiceLineItem
    extra = 0
    fields = (
        "position",
        "description",
        "quantity",
        "unit",
        "rate",
        "discount_type",
        "discount_value",
        "gross_amount",
        "discount_amount",
        "net_amount",
    )
    readonly_fields = ("gross_amount", "discount_amount", "net_amount")


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = (
        "number",
        "invoice_date",
        "customer_name",
        "customer_phone",
        "customer_city",
        "grand_total",
        "payment_badge",
        "balance",
        "due_date",
        "print_count",
    )
    list_filter = ("payment_status", "invoice_date", "customer_city")
    search_fields = ("number", "customer_name", "customer_phone", "customer_email")
    date_hierarchy = "invoice_date"
    inlines = [InvoiceLineItemInline]
    readonly_fields = (
        "number",
        "pdf_path",
        "pdf_saved_at",
        "subtotal",
        "total_discount",
        "grand_total",
        "balance",
        "print_count",
        "last_printed_at",
        "created_by",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        ("Bill", {"fields": ("number", "invoice_date", "customer")}),
        (
            "Customer snapshot",
            {
                "fields": (
                    "customer_name",
                    "customer_phone",
                    "customer_email",
                    "customer_city",
                )
            },
        ),
        ("Amounts", {"fields": ("subtotal", "total_discount", "grand_total")}),
        (
            "Payment",
            {"fields": ("payment_status", "paid_amount", "balance", "due_date")},
        ),
        ("Other", {"fields": ("notes", "print_count", "last_printed_at")}),
        ("Saved document", {"fields": ("pdf_path", "pdf_saved_at")}),
        ("Audit", {"fields": ("created_by", "created_at", "updated_at")}),
    )
    actions = ["recalculate_totals"]

    @admin.display(description="Payment", ordering="payment_status")
    def payment_badge(self, obj):
        colour = {
            "PAID": "#12805c",
            "OVERPAID": "#1a56db",
            "UNDERPAID": "#b45309",
            "PENDING": "#b42318",
        }.get(obj.payment_status, "#555")
        return format_html(
            '<b style="color:{}">{}</b>', colour, obj.get_payment_status_display()
        )

    @admin.action(description="Recalculate totals from line items")
    def recalculate_totals(self, request, queryset):
        for invoice in queryset:
            invoice.recalculate()
        self.message_user(request, f"Recalculated {queryset.count()} bill(s).")


@admin.register(PrintJob)
class PrintJobAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "invoice",
        "backend",
        "printer_name",
        "copies",
        "status",
        "requested_by",
    )
    list_filter = ("status", "backend")
    search_fields = ("invoice__number", "message")
    readonly_fields = [f.name for f in PrintJob._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(InvoiceSequence)
class InvoiceSequenceAdmin(admin.ModelAdmin):
    list_display = ("prefix", "period", "last_number")
    readonly_fields = ("prefix", "period")
