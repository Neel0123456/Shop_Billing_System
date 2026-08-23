"""Small hand-rolled factories. No extra dependency, no magic."""
from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone

from billing.models import (
    Customer,
    DiscountType,
    Invoice,
    InvoiceLineItem,
    PaymentStatus,
    ShopProfile,
)

User = get_user_model()


def make_user(email="owner@shop.test", password="billing-pass-123", **kwargs) -> User:
    return User.objects.create_user(
        email=email, password=password, full_name=kwargs.pop("full_name", "Shop Owner"), **kwargs
    )


def make_shop(**kwargs) -> ShopProfile:
    shop = ShopProfile.load()
    defaults = {
        "name": "Shree Traders",
        "tagline": "Quality goods since 1998",
        "address_line1": "12, Market Road",
        "city": "Himmatnagar",
        "state": "Gujarat",
        "pincode": "383001",
        "phone": "02772 240000",
        "email": "shop@shree.test",
        "gstin": "24ABCDE1234F1Z5",
    }
    defaults.update(kwargs)
    for key, value in defaults.items():
        setattr(shop, key, value)
    shop.save()
    return shop


def make_customer(**kwargs) -> Customer:
    defaults = {
        "name": "Ramesh Patel",
        "phone": "9876543210",
        "email": "ramesh@example.test",
        "city": "Himmatnagar",
    }
    defaults.update(kwargs)
    return Customer.objects.create(**defaults)


def make_invoice(
    *,
    customer: Customer | None = None,
    user=None,
    items: list[dict] | None = None,
    payment_status: str = PaymentStatus.PAID,
    paid_amount: Decimal | None = None,
    due_date=None,
    invoice_date=None,
    **kwargs,
) -> Invoice:
    """Create a fully-computed invoice, exactly the way the view does."""
    customer = customer or make_customer()
    invoice = Invoice.objects.create(
        customer=customer,
        customer_name=customer.name,
        customer_phone=customer.phone,
        customer_email=customer.email,
        customer_city=customer.city,
        invoice_date=invoice_date or timezone.localdate(),
        payment_status=payment_status,
        due_date=due_date,
        created_by=user,
        **kwargs,
    )

    for position, spec in enumerate(items or [{"description": "Cotton shirt", "rate": "500.00"}], 1):
        InvoiceLineItem.objects.create(
            invoice=invoice,
            position=position,
            description=spec.get("description", f"Item {position}"),
            quantity=Decimal(str(spec.get("quantity", 1))),
            unit=spec.get("unit", ""),
            rate=Decimal(str(spec["rate"])),
            discount_type=spec.get("discount_type", DiscountType.PERCENT),
            discount_value=Decimal(str(spec.get("discount_value", 0))),
        )

    invoice.recalculate(commit=False)
    if paid_amount is not None:
        invoice.paid_amount = Decimal(str(paid_amount))
    invoice.apply_payment_status()
    invoice.save()
    return invoice


def invoice_post_data(**overrides) -> dict:
    """A valid POST body for the invoice create view (header + one line)."""
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
        # inline formset management data
        "items-TOTAL_FORMS": "1",
        "items-INITIAL_FORMS": "0",
        "items-MIN_NUM_FORMS": "1",
        "items-MAX_NUM_FORMS": "1000",
        "items-0-id": "",
        "items-0-description": "Cotton shirt",
        "items-0-quantity": "2",
        "items-0-unit": "pcs",
        "items-0-rate": "500.00",
        "items-0-discount_type": DiscountType.PERCENT,
        "items-0-discount_value": "",
    }
    data.update(overrides)
    return data


def add_line(data: dict, index: int, **fields) -> dict:
    """Append another line-item row to a POST body built above."""
    defaults = {
        "id": "",
        "description": f"Extra item {index}",
        "quantity": "1",
        "unit": "",
        "rate": "100.00",
        "discount_type": DiscountType.PERCENT,
        "discount_value": "",
    }
    defaults.update({k: str(v) for k, v in fields.items()})
    for key, value in defaults.items():
        data[f"items-{index}-{key}"] = value
    data["items-TOTAL_FORMS"] = str(max(int(data["items-TOTAL_FORMS"]), index + 1))
    return data


def sample_context() -> dict:
    """
    Realistic context for rendering the Word template outside a request.

    Used by `manage.py docx_template --check` so the shop owner can validate a
    template without creating a bill first.
    """
    from billing.services.context import build_invoice_context

    make_shop()
    invoice = make_invoice(
        items=[
            {"description": "Cotton shirt", "quantity": 2, "unit": "pcs", "rate": "750.00",
             "discount_type": DiscountType.PERCENT, "discount_value": "10"},
            {"description": "Denim trousers", "quantity": 1, "unit": "pcs", "rate": "1450.00",
             "discount_type": DiscountType.AMOUNT, "discount_value": "150"},
            {"description": "Cotton socks", "quantity": 3, "unit": "pair", "rate": "120.00"},
        ],
        payment_status=PaymentStatus.UNDERPAID,
        paid_amount="1500.00",
        due_date=timezone.localdate(),
    )
    return build_invoice_context(invoice)
