"""
Fill the database with realistic demo data so the UI can be explored.

    python manage.py seed_demo
    python manage.py seed_demo --bills 40 --reset

Creates a login (owner@shop.test / billing123 by default), a shop profile,
customers and a spread of bills across all four payment states.
"""
from __future__ import annotations

import random
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from billing.models import (
    Customer,
    DiscountType,
    Invoice,
    InvoiceLineItem,
    InvoiceSequence,
    PaymentStatus,
    PrintJob,
    ShopProfile,
)

User = get_user_model()

CITIES = ["Himmatnagar", "Himmatnagar", "Himmatnagar", "Ahmedabad", "Idar", "Prantij", "Modasa"]

NAMES = [
    "Ramesh Patel", "Suresh Shah", "Mahesh Joshi", "Nilesh Desai", "Kiran Trivedi",
    "Jayesh Modi", "Bhavesh Soni", "Hardik Chauhan", "Vipul Mehta", "Dharmesh Bhatt",
    "Alpesh Rana", "Chirag Prajapati", "Manish Solanki", "Rakesh Vyas", "Tushar Pandya",
]

PRODUCTS = [
    ("Cotton shirt (full sleeve)", "pcs", 650, 950),
    ("Cotton shirt (half sleeve)", "pcs", 550, 800),
    ("Denim trousers", "pcs", 1100, 1800),
    ("Formal trousers", "pcs", 900, 1500),
    ("Cotton socks", "pair", 90, 180),
    ("Leather belt", "pcs", 350, 700),
    ("Cotton vest", "pcs", 150, 300),
    ("Handkerchief set", "set", 100, 220),
    ("Woollen sweater", "pcs", 900, 2200),
    ("Cotton towel", "pcs", 200, 550),
    ("Bed sheet (double)", "set", 700, 1600),
    ("School uniform set", "set", 800, 1400),
]


class Command(BaseCommand):
    help = "Create demo users, customers and bills for exploring the app."

    def add_arguments(self, parser):
        parser.add_argument("--bills", type=int, default=25, help="How many bills to create.")
        parser.add_argument("--email", default="owner@shop.test")
        parser.add_argument("--password", default="billing123")
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete existing bills, customers and print jobs first.",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Only delete billing data and stop. Use this to empty the "
                 "book before the shop starts billing for real.",
        )
        parser.add_argument("--seed", type=int, default=20260823, help="RNG seed.")

    @transaction.atomic
    def handle(self, *args, **options):
        rng = random.Random(options["seed"])

        if options["reset"] or options["clear"]:
            PrintJob.objects.all().delete()
            InvoiceLineItem.objects.all().delete()
            Invoice.objects.all().delete()
            Customer.objects.all().delete()
            InvoiceSequence.objects.all().delete()
            self.stdout.write(self.style.WARNING("Cleared all bills and customers."))

        if options["clear"]:
            # Logins and the shop profile are deliberately left alone.
            self.stdout.write(
                self.style.SUCCESS(
                    "The book is empty. Bill numbering restarts at 0001.\n"
                    "Archived PDFs on disk were not touched - delete them by hand "
                    "if you want a clean folder too."
                )
            )
            return

        user = self._ensure_user(options["email"], options["password"])
        self._ensure_shop()

        customers = self._ensure_customers(rng)
        created = self._create_bills(rng, user, customers, options["bills"])

        self.stdout.write(self.style.SUCCESS(f"\nCreated {created} demo bill(s)."))
        self.stdout.write(f"  Sign in with:  {options['email']}  /  {options['password']}")
        self.stdout.write("  Start the server:  python manage.py runserver\n")

    # -- pieces ------------------------------------------------------------
    def _ensure_user(self, email: str, password: str):
        user, created = User.objects.get_or_create(
            email=email.lower(),
            defaults={"full_name": "Shop Owner", "is_staff": True, "is_superuser": True},
        )
        if created:
            user.set_password(password)
            user.save()
            self.stdout.write(self.style.SUCCESS(f"Created login {email}"))
        else:
            self.stdout.write(f"Login {email} already exists (password unchanged).")
        return user

    def _ensure_shop(self):
        shop = ShopProfile.load()
        if shop.name in ("", "My Shop"):
            shop.name = "Shree Traders"
            shop.tagline = "Readymade garments and household linen"
            shop.address_line1 = "Shop 12, Motipura Main Road"
            shop.address_line2 = "Opp. Bus Station"
            shop.city = "Himmatnagar"
            shop.state = "Gujarat"
            shop.pincode = "383001"
            shop.phone = "02772 240000"
            shop.alt_phone = "98250 11223"
            shop.email = "shreetraders@example.com"
            shop.gstin = "24ABCDE1234F1Z5"
            shop.invoice_footer_note = "Thank you for shopping with us!"
            shop.terms = "Goods once sold are exchangeable within 7 days with the bill."
            shop.save()
            self.stdout.write(self.style.SUCCESS("Filled in the shop profile."))
        return shop

    def _ensure_customers(self, rng):
        customers = []
        for index, name in enumerate(NAMES):
            phone = f"98{rng.randint(10000000, 99999999)}"
            slug = name.split()[0].lower()
            customer, _ = Customer.objects.get_or_create(
                phone=phone,
                defaults={
                    "name": name,
                    "email": f"{slug}@example.com" if index % 3 else "",
                    "city": rng.choice(CITIES),
                },
            )
            customers.append(customer)
        self.stdout.write(f"Customers ready: {len(customers)}")
        return customers

    def _create_bills(self, rng, user, customers, count: int) -> int:
        today = timezone.localdate()
        created = 0

        for offset in range(count):
            customer = rng.choice(customers)
            bill_date = today - timedelta(days=rng.randint(0, 75))

            invoice = Invoice.objects.create(
                customer=customer,
                customer_name=customer.name,
                customer_phone=customer.phone,
                customer_email=customer.email,
                customer_city=customer.city,
                invoice_date=bill_date,
                created_by=user,
            )

            for position in range(1, rng.randint(1, 5) + 1):
                description, unit, low, high = rng.choice(PRODUCTS)
                discount_type = rng.choice(
                    [DiscountType.PERCENT, DiscountType.AMOUNT, DiscountType.PERCENT]
                )
                # Roughly half the lines carry no discount at all.
                if rng.random() < 0.5:
                    discount_value = Decimal("0")
                elif discount_type == DiscountType.PERCENT:
                    discount_value = Decimal(rng.choice([5, 10, 12, 15, 20]))
                else:
                    discount_value = Decimal(rng.choice([25, 50, 100, 150]))

                InvoiceLineItem.objects.create(
                    invoice=invoice,
                    position=position,
                    description=description,
                    unit=unit,
                    quantity=Decimal(rng.randint(1, 4)),
                    rate=Decimal(rng.randint(low, high)),
                    discount_type=discount_type,
                    discount_value=discount_value,
                )

            invoice.recalculate(commit=False)

            # A realistic spread across the four payment states.
            roll = rng.random()
            if roll < 0.55:
                invoice.payment_status = PaymentStatus.PAID
            elif roll < 0.75:
                invoice.payment_status = PaymentStatus.UNDERPAID
                invoice.paid_amount = (invoice.grand_total * Decimal("0.4")).quantize(
                    Decimal("0.01")
                )
                invoice.due_date = bill_date + timedelta(days=rng.choice([7, 15, 30]))
            elif roll < 0.9:
                invoice.payment_status = PaymentStatus.PENDING
                invoice.due_date = bill_date + timedelta(days=rng.choice([10, 20, 30]))
            else:
                invoice.payment_status = PaymentStatus.OVERPAID
                invoice.paid_amount = invoice.grand_total + Decimal(rng.choice([50, 100, 200]))

            invoice.apply_payment_status()
            invoice.save()
            created += 1

        return created
