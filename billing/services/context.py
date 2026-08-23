"""
The single context dictionary handed to the Word template and the HTML fallback.

Every value is a *pre-formatted string* where it makes sense, so the Word
template stays free of logic: the shop owner only drops `{{ placeholders }}`
into his document and never has to think about rounding or currency symbols.

Placeholder reference (also printed by `manage.py docx_template --list`):

  Shop        shop_name, shop_owner, shop_tagline, shop_address, shop_address_line1,
              shop_address_line2, shop_city, shop_state, shop_pincode,
              shop_phone, shop_alt_phone, shop_email, shop_gstin,
              shop_footer_note, shop_terms
  Bill        invoice_number, invoice_date, invoice_date_long, created_on,
              created_at, financial_year
  Customer    customer_name, customer_phone, customer_email, customer_city,
              customer_address, has_email
  Items       items  ->  list of dicts with keys:
              sr, description, code, qty, unit, rate, gross,
              discount, discount_label, has_discount, amount, rate_block
  Totals      subtotal, total_discount, has_discount, grand_total,
              grand_total_words, item_count, total_qty
  Payment     payment_status, payment_status_label, paid_amount,
              pending_amount, overpaid_amount, has_pending, has_overpaid,
              due_date, has_due_date, notes
  Misc        currency, printed_on, printed_by
"""
from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from billing.utils import amount_in_words, format_inr, trim_decimal

ZERO = Decimal("0.00")


def _money(value, symbol: str = "") -> str:
    return format_inr(value, symbol=symbol)


def build_invoice_context(invoice, *, printed_by=None, for_html: bool = False) -> dict:
    """Flatten an Invoice into template-ready values."""
    from billing.models import InvoiceSequence, PaymentStatus, ShopProfile

    shop = ShopProfile.load()
    currency = settings.CURRENCY_SYMBOL

    items = []
    total_qty = ZERO
    for index, item in enumerate(invoice.items.all(), start=1):
        total_qty += item.quantity

        # What sits in the rate cell of the bill: the rate, then the discount
        # subtracted right underneath it, then nothing else. Exactly the layout
        # asked for - and the discount line disappears entirely when it is 0.
        rate_block = _money(item.rate)
        if item.has_discount:
            rate_block = f"{rate_block}\n{item.discount_label}"

        items.append(
            {
                "sr": index,
                "description": item.description,
                "code": item.hsn_code,
                "qty": trim_decimal(item.quantity),
                "unit": item.unit,
                "rate": _money(item.rate),
                "gross": _money(item.gross_amount),
                "discount": _money(item.discount_amount) if item.has_discount else "",
                "discount_label": item.discount_label,
                "has_discount": item.has_discount,
                "amount": _money(item.net_amount),
                "rate_block": rate_block,
                "obj": item if for_html else None,
            }
        )

    status = PaymentStatus(invoice.payment_status)

    return {
        # --- shop ---------------------------------------------------------
        "shop": shop if for_html else None,
        "shop_name": shop.name,
        "shop_owner": shop.owner_name,
        "shop_tagline": shop.tagline,
        "shop_address": ", ".join(shop.address_block),
        "shop_address_line1": shop.address_line1,
        "shop_address_line2": shop.address_line2,
        "shop_city": shop.city,
        "shop_state": shop.state,
        "shop_pincode": shop.pincode,
        "shop_phone": shop.phone,
        "shop_alt_phone": shop.alt_phone,
        "shop_email": shop.email,
        "shop_gstin": shop.gstin,
        "shop_footer_note": shop.invoice_footer_note,
        "shop_terms": shop.terms,
        # --- bill ---------------------------------------------------------
        "invoice": invoice if for_html else None,
        "invoice_number": invoice.number,
        # The bill date: defaults to the day the bill is made, stored in the DB
        # and always printed on the document.
        "invoice_date": invoice.invoice_date.strftime("%d-%m-%Y"),
        "invoice_date_long": invoice.invoice_date.strftime("%d %B %Y"),
        # When the row was actually entered into the system (audit trail).
        "created_on": timezone.localtime(invoice.created_at).strftime("%d-%m-%Y")
        if invoice.created_at
        else "",
        "created_at": timezone.localtime(invoice.created_at).strftime("%d-%m-%Y %I:%M %p")
        if invoice.created_at
        else "",
        "financial_year": InvoiceSequence.financial_year(invoice.invoice_date),
        # --- customer -----------------------------------------------------
        "customer_name": invoice.customer_name,
        "customer_phone": invoice.customer_phone,
        "customer_email": invoice.customer_email or "-",
        "has_email": bool(invoice.customer_email),
        "customer_city": invoice.customer_city,
        "customer_address": getattr(invoice.customer, "address", "") or "",
        # --- items --------------------------------------------------------
        "items": items,
        "item_count": len(items),
        "total_qty": trim_decimal(total_qty),
        # --- totals -------------------------------------------------------
        "subtotal": _money(invoice.subtotal),
        "total_discount": _money(invoice.total_discount),
        "total_discount_negative": f"-{_money(invoice.total_discount)}",
        "has_discount": invoice.total_discount > ZERO,
        "grand_total": _money(invoice.grand_total),
        "grand_total_words": amount_in_words(invoice.grand_total),
        # --- payment ------------------------------------------------------
        "payment_status": status.value,
        "payment_status_label": status.label,
        "paid_amount": _money(invoice.paid_amount),
        "pending_amount": _money(invoice.amount_pending),
        "overpaid_amount": _money(invoice.amount_overpaid),
        "has_pending": invoice.amount_pending > ZERO,
        "has_overpaid": invoice.amount_overpaid > ZERO,
        "due_date": invoice.due_date.strftime("%d-%m-%Y") if invoice.due_date else "",
        "has_due_date": bool(invoice.due_date),
        "notes": invoice.notes,
        # --- misc ---------------------------------------------------------
        "currency": currency,
        "printed_on": timezone.localtime().strftime("%d-%m-%Y %I:%M %p"),
        "printed_by": getattr(printed_by, "full_name", "") or getattr(printed_by, "email", ""),
        "show_signature_line": shop.show_signature_line,
    }


PLACEHOLDER_GROUPS = {
    "Shop": [
        "shop_name", "shop_owner", "shop_tagline", "shop_address", "shop_address_line1",
        "shop_address_line2", "shop_city", "shop_state", "shop_pincode",
        "shop_phone", "shop_alt_phone", "shop_email", "shop_gstin",
        "shop_footer_note", "shop_terms",
    ],
    "Bill": [
        "invoice_number", "invoice_date", "invoice_date_long", "created_on",
        "created_at", "financial_year",
    ],
    "Customer": [
        "customer_name", "customer_phone", "customer_email", "customer_city",
        "customer_address",
    ],
    "Line item (inside the loop)": [
        "item.sr", "item.description", "item.code", "item.qty", "item.unit",
        "item.rate", "item.gross", "item.discount", "item.discount_label",
        "item.amount", "item.rate_block",
    ],
    "Totals": [
        "subtotal", "total_discount", "grand_total", "grand_total_words",
        "item_count", "total_qty",
    ],
    "Payment": [
        "payment_status_label", "paid_amount", "pending_amount",
        "overpaid_amount", "due_date", "notes",
    ],
    "Misc": ["currency", "printed_on", "printed_by"],
}
