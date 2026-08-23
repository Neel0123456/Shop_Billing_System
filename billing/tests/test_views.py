"""View tests: auth gates, the save / save-and-print flows, filters, PDFs."""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.models import (
    Customer,
    DiscountType,
    Invoice,
    InvoiceLineItem,
    PaymentStatus,
    PrintJob,
)

from .factories import (
    add_line,
    invoice_post_data,
    make_customer,
    make_invoice,
    make_shop,
    make_user,
)

TEST_SETTINGS = dict(DOCUMENT_ENGINE="html", PRINT_BACKEND="dummy")


@override_settings(**TEST_SETTINGS)
class AuthGateTests(TestCase):
    """Every billing page must require a login."""

    def setUp(self):
        make_shop()
        self.invoice = make_invoice()

    def test_all_pages_redirect_anonymous_users_to_login(self):
        urls = [
            reverse("billing:invoice_list"),
            reverse("billing:invoice_create"),
            reverse("billing:invoice_detail", args=[self.invoice.pk]),
            reverse("billing:invoice_update", args=[self.invoice.pk]),
            reverse("billing:invoice_pdf", args=[self.invoice.pk]),
            reverse("billing:invoice_preview", args=[self.invoice.pk]),
            reverse("billing:shop_profile"),
            reverse("billing:print_settings"),
            reverse("billing:customer_lookup"),
        ]
        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn("/accounts/login/", response.url)

    def test_print_requires_login(self):
        response = self.client.post(reverse("billing:invoice_print", args=[self.invoice.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)

    def test_root_redirects_to_the_bill_list(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("billing:invoice_list"))


@override_settings(**TEST_SETTINGS)
class InvoiceCreateViewTests(TestCase):
    def setUp(self):
        make_shop()
        self.user = make_user()
        self.client.force_login(self.user)
        self.url = reverse("billing:invoice_create")

    def test_form_page_renders(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Customer name")
        self.assertContains(response, "Save &amp; Print")
        # All four payment options are offered, and only those four.
        for value in ["PAID", "OVERPAID", "UNDERPAID", "PENDING"]:
            self.assertContains(response, f'value="{value}"')

    def test_saving_creates_the_invoice_with_computed_totals(self):
        data = invoice_post_data(**{"items-0-quantity": "2", "items-0-rate": "500",
                                    "items-0-discount_value": "10"})
        response = self.client.post(self.url, data)

        invoice = Invoice.objects.get()
        self.assertRedirects(response, invoice.get_absolute_url())
        self.assertEqual(invoice.subtotal, Decimal("1000.00"))
        self.assertEqual(invoice.total_discount, Decimal("100.00"))
        self.assertEqual(invoice.grand_total, Decimal("900.00"))
        self.assertEqual(invoice.paid_amount, Decimal("900.00"))
        self.assertEqual(invoice.payment_status, PaymentStatus.PAID)
        self.assertEqual(invoice.created_by, self.user)

    def test_bill_date_and_created_at_are_stored(self):
        self.client.post(self.url, invoice_post_data())
        invoice = Invoice.objects.get()
        self.assertEqual(invoice.invoice_date, timezone.localdate())
        self.assertIsNotNone(invoice.created_at)

    def test_customer_is_created_and_city_defaults(self):
        self.client.post(self.url, invoice_post_data(customer_city=""))
        customer = Customer.objects.get()
        self.assertEqual(customer.phone, "9876543210")
        self.assertEqual(customer.city, "Himmatnagar")
        self.assertEqual(Invoice.objects.get().customer_city, "Himmatnagar")

    def test_supplied_city_is_kept(self):
        self.client.post(self.url, invoice_post_data(customer_city="Ahmedabad"))
        self.assertEqual(Invoice.objects.get().customer_city, "Ahmedabad")

    def test_multiple_line_items_are_all_saved_in_order(self):
        data = invoice_post_data(**{"items-0-description": "First", "items-0-rate": "100",
                                    "items-0-quantity": "1"})
        add_line(data, 1, description="Second", rate="200")
        add_line(data, 2, description="Third", rate="300")

        self.client.post(self.url, data)
        invoice = Invoice.objects.get()
        descriptions = list(invoice.items.values_list("description", flat=True))
        self.assertEqual(descriptions, ["First", "Second", "Third"])
        self.assertEqual([i.position for i in invoice.items.all()], [1, 2, 3])
        self.assertEqual(invoice.grand_total, Decimal("600.00"))

    def test_amount_discount_is_subtracted(self):
        data = invoice_post_data(**{
            "items-0-rate": "1000",
            "items-0-quantity": "1",
            "items-0-discount_type": DiscountType.AMOUNT,
            "items-0-discount_value": "150",
        })
        self.client.post(self.url, data)
        item = InvoiceLineItem.objects.get()
        self.assertEqual(item.discount_amount, Decimal("150.00"))
        self.assertEqual(item.net_amount, Decimal("850.00"))
        self.assertEqual(item.discount_label, "-150.00")

    def test_invalid_bill_is_redisplayed_and_saves_nothing(self):
        data = invoice_post_data(**{"items-0-rate": ""})
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Invoice.objects.count(), 0)
        self.assertEqual(Customer.objects.count(), 0)

    def test_pending_without_a_due_date_is_rejected(self):
        data = invoice_post_data(payment_status=PaymentStatus.PENDING)
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Invoice.objects.count(), 0)
        self.assertContains(response, "due date is required")

    def test_underpaid_bill_records_the_shortfall_and_due_date(self):
        due = timezone.localdate() + timedelta(days=10)
        data = invoice_post_data(
            payment_status=PaymentStatus.UNDERPAID,
            paid_amount="400",
            due_date=due.isoformat(),
            **{"items-0-quantity": "1", "items-0-rate": "1000"},
        )
        self.client.post(self.url, data)
        invoice = Invoice.objects.get()
        self.assertEqual(invoice.payment_status, PaymentStatus.UNDERPAID)
        self.assertEqual(invoice.paid_amount, Decimal("400.00"))
        self.assertEqual(invoice.amount_pending, Decimal("600.00"))
        self.assertEqual(invoice.due_date, due)

    def test_overpaid_bill_records_the_excess(self):
        data = invoice_post_data(
            payment_status=PaymentStatus.OVERPAID,
            paid_amount="1200",
            **{"items-0-quantity": "1", "items-0-rate": "1000"},
        )
        self.client.post(self.url, data)
        invoice = Invoice.objects.get()
        self.assertEqual(invoice.amount_overpaid, Decimal("200.00"))
        self.assertIsNone(invoice.due_date)

    def test_pdf_is_archived_on_save(self):
        from django.conf import settings

        self.client.post(self.url, invoice_post_data())
        invoice = Invoice.objects.get()
        archived = list(settings.INVOICE_PDF_DIR.rglob(invoice.pdf_filename))
        self.assertTrue(archived, "the saved bill should be written to INVOICE_PDF_DIR")


@override_settings(**TEST_SETTINGS)
class SaveAndPrintTests(TestCase):
    def setUp(self):
        make_shop()
        self.user = make_user()
        self.client.force_login(self.user)
        self.url = reverse("billing:invoice_create")

    def test_save_and_print_records_a_print_job(self):
        data = invoice_post_data(action="save_print")
        response = self.client.post(self.url, data)

        invoice = Invoice.objects.get()
        self.assertRedirects(response, invoice.get_absolute_url())

        job = PrintJob.objects.get()
        self.assertEqual(job.invoice, invoice)
        self.assertEqual(job.backend, "dummy")
        self.assertEqual(job.status, PrintJob.Status.MOCKED)
        self.assertEqual(job.requested_by, self.user)
        self.assertTrue(job.succeeded)

    def test_save_and_print_increments_the_print_counter(self):
        self.client.post(self.url, invoice_post_data(action="save_print"))
        invoice = Invoice.objects.get()
        self.assertEqual(invoice.print_count, 1)
        self.assertIsNotNone(invoice.last_printed_at)

    def test_plain_save_does_not_print(self):
        self.client.post(self.url, invoice_post_data(action="save"))
        self.assertEqual(PrintJob.objects.count(), 0)
        self.assertEqual(Invoice.objects.get().print_count, 0)

    def test_the_mock_backend_writes_the_pdf_it_would_have_printed(self):
        from django.conf import settings

        self.client.post(self.url, invoice_post_data(action="save_print"))
        invoice = Invoice.objects.get()

        # Match this bill only: other tests share the spool folder.
        spooled = list(settings.PRINT_SPOOL_DIR.glob(f"*{invoice.pdf_filename}"))
        self.assertTrue(spooled, "the dummy backend should leave proof in the spool folder")
        self.assertTrue(spooled[0].read_bytes().startswith(b"%PDF"))
        self.assertGreater(spooled[0].stat().st_size, 500)

    def test_reprint_from_the_detail_page(self):
        invoice = make_invoice()
        response = self.client.post(reverse("billing:invoice_print", args=[invoice.pk]))
        self.assertRedirects(response, invoice.get_absolute_url())
        invoice.refresh_from_db()
        self.assertEqual(invoice.print_count, 1)
        self.assertEqual(PrintJob.objects.count(), 1)

    def test_reprint_via_ajax_returns_json(self):
        invoice = make_invoice()
        response = self.client.post(
            reverse("billing:invoice_print", args=[invoice.pk]),
            headers={"x-requested-with": "XMLHttpRequest"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["print_count"], 1)

    @override_settings(PRINT_BACKEND="browser")
    def test_browser_backend_redirects_to_the_dialog_page(self):
        invoice = make_invoice()
        response = self.client.post(reverse("billing:invoice_print", args=[invoice.pk]))
        self.assertRedirects(
            response, reverse("billing:invoice_browser_print", args=[invoice.pk])
        )


@override_settings(**TEST_SETTINGS)
class InvoiceUpdateViewTests(TestCase):
    def setUp(self):
        make_shop()
        self.user = make_user()
        self.client.force_login(self.user)
        self.invoice = make_invoice(
            items=[{"description": "Original", "rate": "500"}], user=self.user
        )

    def test_edit_page_renders_existing_data(self):
        response = self.client.get(reverse("billing:invoice_update", args=[self.invoice.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Original")
        self.assertContains(response, self.invoice.customer_phone)

    def test_editing_recalculates_the_totals(self):
        item = self.invoice.items.get()
        data = invoice_post_data(**{
            "items-INITIAL_FORMS": "1",
            "items-0-id": str(item.pk),
            "items-0-description": "Changed",
            "items-0-quantity": "1",
            "items-0-rate": "900",
            "items-0-discount_value": "",
        })
        response = self.client.post(
            reverse("billing:invoice_update", args=[self.invoice.pk]), data
        )
        self.assertEqual(response.status_code, 302)

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.grand_total, Decimal("900.00"))
        self.assertEqual(self.invoice.items.get().description, "Changed")

    def test_the_bill_number_never_changes_on_edit(self):
        original = self.invoice.number
        item = self.invoice.items.get()
        data = invoice_post_data(**{
            "items-INITIAL_FORMS": "1",
            "items-0-id": str(item.pk),
            "items-0-rate": "111",
        })
        self.client.post(reverse("billing:invoice_update", args=[self.invoice.pk]), data)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.number, original)

    def test_deleting_a_row_removes_it(self):
        second = InvoiceLineItem.objects.create(
            invoice=self.invoice, position=2, description="Second", rate=Decimal("300")
        )
        self.invoice.recalculate()
        first = self.invoice.items.first()

        data = invoice_post_data(**{
            "items-TOTAL_FORMS": "2",
            "items-INITIAL_FORMS": "2",
            "items-0-id": str(first.pk),
            "items-0-description": first.description,
            "items-0-quantity": "1",
            "items-0-rate": str(first.rate),
            "items-0-discount_type": DiscountType.PERCENT,
            "items-0-discount_value": "",
            "items-1-id": str(second.pk),
            "items-1-description": second.description,
            "items-1-quantity": "1",
            "items-1-rate": str(second.rate),
            "items-1-discount_type": DiscountType.PERCENT,
            "items-1-discount_value": "",
            "items-1-DELETE": "on",
        })
        self.client.post(reverse("billing:invoice_update", args=[self.invoice.pk]), data)

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.items.count(), 1)
        self.assertEqual(self.invoice.grand_total, Decimal("500.00"))


@override_settings(**TEST_SETTINGS)
class InvoiceListFilterTests(TestCase):
    def setUp(self):
        make_shop()
        self.user = make_user()
        self.client.force_login(self.user)
        self.url = reverse("billing:invoice_list")

        self.a = make_invoice(
            customer=make_customer(name="Ramesh Patel", phone="9876500001",
                                   email="ramesh@a.test", city="Himmatnagar"),
            items=[{"description": "A", "rate": "1000"}],
            payment_status=PaymentStatus.PAID,
        )
        self.b = make_invoice(
            customer=make_customer(name="Suresh Shah", phone="9876500002",
                                   email="suresh@b.test", city="Ahmedabad"),
            items=[{"description": "B", "rate": "2000"}],
            payment_status=PaymentStatus.PENDING,
            due_date=timezone.localdate() - timedelta(days=3),
        )
        self.c = make_invoice(
            customer=make_customer(name="Mahesh Joshi", phone="9998800003",
                                   email="mahesh@c.test", city="Surat"),
            items=[{"description": "C", "rate": "3000"}],
            payment_status=PaymentStatus.UNDERPAID,
            paid_amount="1000",
            due_date=timezone.localdate() + timedelta(days=4),
        )

    def _numbers(self, **params):
        response = self.client.get(self.url, params)
        self.assertEqual(response.status_code, 200)
        return {inv.number for inv in response.context["invoices"]}

    def test_unfiltered_shows_everything(self):
        self.assertEqual(len(self._numbers()), 3)

    def test_filter_by_name(self):
        self.assertEqual(self._numbers(name="ramesh"), {self.a.number})

    def test_filter_by_phone(self):
        self.assertEqual(self._numbers(phone="9876500002"), {self.b.number})

    def test_filter_by_partial_phone(self):
        self.assertEqual(
            self._numbers(phone="98765"), {self.a.number, self.b.number}
        )

    def test_filter_by_email(self):
        self.assertEqual(self._numbers(email="mahesh@"), {self.c.number})

    def test_filter_by_city(self):
        self.assertEqual(self._numbers(city="ahmedabad"), {self.b.number})

    def test_filter_by_outstanding(self):
        self.assertEqual(
            self._numbers(payment="OUTSTANDING"), {self.b.number, self.c.number}
        )

    def test_filter_by_pending_only(self):
        self.assertEqual(self._numbers(payment=PaymentStatus.PENDING), {self.b.number})

    def test_filter_by_underpaid_only(self):
        self.assertEqual(self._numbers(payment=PaymentStatus.UNDERPAID), {self.c.number})

    def test_filter_by_overdue(self):
        self.assertEqual(self._numbers(payment="OVERDUE"), {self.b.number})

    def test_filter_by_amount_range(self):
        self.assertEqual(
            self._numbers(min_amount="1500", max_amount="2500"), {self.b.number}
        )

    def test_filter_by_bill_date_range(self):
        today = timezone.localdate().isoformat()
        self.assertEqual(len(self._numbers(date_from=today, date_to=today)), 3)

    def test_filter_by_created_on(self):
        today = timezone.localdate().isoformat()
        self.assertEqual(len(self._numbers(created_on=today)), 3)

    def test_created_on_excludes_other_days(self):
        tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
        self.assertEqual(self._numbers(created_on=tomorrow), set())

    def test_free_text_search_covers_every_column(self):
        self.assertEqual(self._numbers(q="Surat"), {self.c.number})
        self.assertEqual(self._numbers(q="suresh@b.test"), {self.b.number})
        self.assertEqual(self._numbers(q=self.a.number), {self.a.number})

    def test_filters_combine(self):
        self.assertEqual(
            self._numbers(city="Surat", payment="OUTSTANDING"), {self.c.number}
        )
        self.assertEqual(self._numbers(city="Surat", payment=PaymentStatus.PAID), set())

    def test_summary_totals_reflect_the_filter(self):
        response = self.client.get(self.url)
        summary = response.context["summary"]
        self.assertEqual(summary["count"], 3)
        self.assertEqual(summary["billed"], Decimal("6000.00"))
        self.assertEqual(summary["received"], Decimal("2000.00"))
        self.assertEqual(summary["pending"], Decimal("4000.00"))

    def test_bad_date_range_shows_an_error_and_no_crash(self):
        response = self.client.get(self.url, {"date_from": "2026-05-10", "date_to": "2026-05-01"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["filter_form"].errors)


@override_settings(**TEST_SETTINGS)
class InvoiceDetailAndDocumentTests(TestCase):
    def setUp(self):
        make_shop()
        self.user = make_user()
        self.client.force_login(self.user)
        self.invoice = make_invoice(
            items=[
                {"description": "Shirt", "quantity": 2, "rate": "750",
                 "discount_type": DiscountType.PERCENT, "discount_value": "10"},
                {"description": "Socks", "rate": "120"},
            ]
        )

    def test_detail_page_shows_the_bill(self):
        response = self.client.get(self.invoice.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.invoice.number)
        self.assertContains(response, "Shirt")
        self.assertContains(response, "-10% (-150.00)")

    def test_detail_page_shows_the_bill_date(self):
        response = self.client.get(self.invoice.get_absolute_url())
        self.assertContains(response, self.invoice.invoice_date.strftime("%d %b %Y"))

    def test_pdf_download_returns_a_pdf(self):
        response = self.client.get(
            reverse("billing:invoice_pdf", args=[self.invoice.pk]), {"download": "1"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_pdf_inline_view(self):
        response = self.client.get(reverse("billing:invoice_pdf", args=[self.invoice.pk]))
        self.assertIn("inline", response["Content-Disposition"])

    def test_html_preview_contains_the_bill_details(self):
        response = self.client.get(reverse("billing:invoice_preview", args=[self.invoice.pk]))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn(self.invoice.number, body)
        self.assertIn("Shirt", body)
        self.assertIn(self.invoice.invoice_date.strftime("%d-%m-%Y"), body)

    def test_zero_discount_is_absent_from_the_printed_bill(self):
        response = self.client.get(reverse("billing:invoice_preview", args=[self.invoice.pk]))
        body = response.content.decode()
        # "Socks" has no discount, so no "-0" / "0%" text may appear for it.
        self.assertNotIn("-0%", body)
        self.assertNotIn("-0.00", body)

    def test_missing_invoice_returns_404(self):
        response = self.client.get(reverse("billing:invoice_detail", args=[999999]))
        self.assertEqual(response.status_code, 404)


@override_settings(**TEST_SETTINGS)
class RecordPaymentTests(TestCase):
    def setUp(self):
        make_shop()
        self.user = make_user()
        self.client.force_login(self.user)
        self.invoice = make_invoice(
            items=[{"description": "A", "rate": "1000"}],
            payment_status=PaymentStatus.PENDING,
            due_date=timezone.localdate() + timedelta(days=7),
        )
        self.url = reverse("billing:invoice_record_payment", args=[self.invoice.pk])

    def test_part_payment_moves_the_bill_to_underpaid(self):
        self.client.post(self.url, {"amount": "400"})
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.payment_status, PaymentStatus.UNDERPAID)
        self.assertEqual(self.invoice.paid_amount, Decimal("400.00"))
        self.assertEqual(self.invoice.amount_pending, Decimal("600.00"))

    def test_full_payment_settles_the_bill(self):
        self.client.post(self.url, {"amount": "1000"})
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.payment_status, PaymentStatus.PAID)
        self.assertIsNone(self.invoice.due_date)
        self.assertEqual(self.invoice.balance, Decimal("0.00"))

    def test_excess_payment_becomes_overpaid(self):
        self.client.post(self.url, {"amount": "1300"})
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.payment_status, PaymentStatus.OVERPAID)
        self.assertEqual(self.invoice.amount_overpaid, Decimal("300.00"))

    def test_payment_on_a_settled_bill_is_rejected(self):
        settled = make_invoice(
            customer=make_customer(phone="9000012345"),
            items=[{"description": "A", "rate": "100"}],
            payment_status=PaymentStatus.PAID,
        )
        self.client.post(
            reverse("billing:invoice_record_payment", args=[settled.pk]), {"amount": "50"}
        )
        settled.refresh_from_db()
        self.assertEqual(settled.paid_amount, Decimal("100.00"))


@override_settings(**TEST_SETTINGS)
class CustomerLookupTests(TestCase):
    def setUp(self):
        make_shop()
        self.client.force_login(make_user())
        self.customer = make_customer(
            name="Ramesh Patel", phone="9876543210", email="r@x.test", city="Surat"
        )

    def test_known_phone_returns_the_customer(self):
        response = self.client.get(
            reverse("billing:customer_lookup"), {"phone": "9876543210"}
        )
        payload = response.json()
        self.assertTrue(payload["found"])
        self.assertEqual(payload["name"], "Ramesh Patel")
        self.assertEqual(payload["city"], "Surat")

    def test_unknown_phone_is_reported_as_not_found(self):
        response = self.client.get(reverse("billing:customer_lookup"), {"phone": "9111111111"})
        self.assertFalse(response.json()["found"])

    def test_short_input_is_ignored(self):
        response = self.client.get(reverse("billing:customer_lookup"), {"phone": "98"})
        self.assertFalse(response.json()["found"])


@override_settings(**TEST_SETTINGS)
class SettingsPageTests(TestCase):
    def setUp(self):
        make_shop()
        self.client.force_login(make_user())

    def test_print_settings_page_reports_the_pipeline(self):
        response = self.client.get(reverse("billing:print_settings"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Word template")
        self.assertContains(response, "dummy")

    def test_shop_profile_can_be_edited(self):
        response = self.client.post(
            reverse("billing:shop_profile"),
            {
                "name": "New Shop Name",
                "tagline": "",
                "address_line1": "1 Road",
                "address_line2": "",
                "city": "Himmatnagar",
                "state": "Gujarat",
                "pincode": "383001",
                "phone": "123456",
                "alt_phone": "",
                "email": "",
                "gstin": "",
                "invoice_footer_note": "Thanks",
                "terms": "",
                "show_signature_line": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        from billing.models import ShopProfile

        self.assertEqual(ShopProfile.load().name, "New Shop Name")

    def test_test_print_without_any_bill_warns_instead_of_crashing(self):
        response = self.client.post(reverse("billing:print_test"))
        self.assertRedirects(response, reverse("billing:print_settings"))
        self.assertEqual(PrintJob.objects.count(), 0)

    def test_test_print_uses_the_latest_bill(self):
        make_invoice()
        response = self.client.post(reverse("billing:print_test"))
        self.assertRedirects(response, reverse("billing:print_settings"))
        self.assertEqual(PrintJob.objects.count(), 1)


@override_settings(**TEST_SETTINGS)
class InvoiceDeleteTests(TestCase):
    def setUp(self):
        make_shop()
        self.client.force_login(make_user())
        self.invoice = make_invoice()

    def test_get_is_not_allowed(self):
        response = self.client.get(reverse("billing:invoice_delete", args=[self.invoice.pk]))
        self.assertEqual(response.status_code, 405)
        self.assertEqual(Invoice.objects.count(), 1)

    def test_post_deletes_the_bill_and_its_items(self):
        response = self.client.post(reverse("billing:invoice_delete", args=[self.invoice.pk]))
        self.assertRedirects(response, reverse("billing:invoice_list"))
        self.assertEqual(Invoice.objects.count(), 0)
        self.assertEqual(InvoiceLineItem.objects.count(), 0)


@override_settings(**TEST_SETTINGS, INVOICE_ARCHIVE_SUBFOLDER="date")
class ArchivePathTests(TestCase):
    """The saved PDF path is recorded and shown to the user."""

    def setUp(self):
        make_shop()
        self.user = make_user()
        self.client.force_login(self.user)

    def test_saving_a_bill_stores_the_pdf_path(self):
        self.client.post(reverse("billing:invoice_create"), invoice_post_data())
        invoice = Invoice.objects.get()

        self.assertTrue(invoice.pdf_path)
        self.assertTrue(invoice.pdf_exists)
        self.assertTrue(invoice.pdf_path.endswith(invoice.pdf_filename))
        self.assertIn(invoice.invoice_date.strftime("%Y-%m-%d"), invoice.pdf_path)

    def test_the_detail_page_shows_where_the_file_was_saved(self):
        self.client.post(reverse("billing:invoice_create"), invoice_post_data())
        invoice = Invoice.objects.get()

        response = self.client.get(invoice.get_absolute_url())
        self.assertContains(response, "Saved on this computer")
        self.assertContains(response, invoice.pdf_filename)
        self.assertContains(response, "File present")

    def test_editing_a_bill_refreshes_the_saved_file(self):
        self.client.post(reverse("billing:invoice_create"), invoice_post_data())
        invoice = Invoice.objects.get()
        first_saved_at = invoice.pdf_saved_at
        item = invoice.items.get()

        self.client.post(
            reverse("billing:invoice_update", args=[invoice.pk]),
            invoice_post_data(**{
                "items-INITIAL_FORMS": "1",
                "items-0-id": str(item.pk),
                "items-0-quantity": "1",
                "items-0-rate": "1234",
            }),
        )
        invoice.refresh_from_db()
        self.assertTrue(invoice.pdf_exists)
        self.assertGreater(invoice.pdf_saved_at, first_saved_at)


@override_settings(**TEST_SETTINGS)
class DuplicateSubmissionTests(TestCase):
    """
    Re-posting the same bill form must never create a second bill.

    Covers the real-world ways it happens: an impatient double click, the
    browser Back button followed by Save again, and Save then Save & Print.
    """

    def setUp(self):
        make_shop()
        self.user = make_user()
        self.client.force_login(self.user)
        self.url = reverse("billing:invoice_create")

    def _form_token(self):
        response = self.client.get(self.url)
        return response.context["form"]["form_token"].value()

    def test_the_form_carries_a_token(self):
        token = self._form_token()
        self.assertTrue(token)
        self.assertEqual(len(token), 32)

    def test_each_fresh_form_gets_a_different_token(self):
        self.assertNotEqual(self._form_token(), self._form_token())

    def test_posting_the_same_form_twice_creates_one_bill(self):
        data = invoice_post_data(form_token=self._form_token())

        first = self.client.post(self.url, data)
        second = self.client.post(self.url, data)

        self.assertEqual(Invoice.objects.count(), 1)
        invoice = Invoice.objects.get()
        self.assertRedirects(first, invoice.get_absolute_url())
        self.assertRedirects(second, invoice.get_absolute_url())

    def test_the_repeat_is_reported_rather_than_silently_ignored(self):
        data = invoice_post_data(form_token=self._form_token())
        self.client.post(self.url, data)
        response = self.client.post(self.url, data, follow=True)
        self.assertContains(response, "already saved")

    def test_save_then_save_and_print_does_not_duplicate(self):
        token = self._form_token()
        self.client.post(self.url, invoice_post_data(form_token=token, action="save"))
        self.client.post(self.url, invoice_post_data(form_token=token, action="save_print"))
        self.assertEqual(Invoice.objects.count(), 1)

    def test_the_totals_are_not_inflated_by_a_repeat(self):
        data = invoice_post_data(
            form_token=self._form_token(),
            **{"items-0-quantity": "1", "items-0-rate": "1620"},
        )
        for _ in range(3):
            self.client.post(self.url, data)

        response = self.client.get(reverse("billing:invoice_list"))
        summary = response.context["summary"]
        self.assertEqual(summary["count"], 1)
        self.assertEqual(summary["billed"], Decimal("1620.00"))

    def test_a_repeat_does_not_print_a_second_time(self):
        token = self._form_token()
        data = invoice_post_data(form_token=token, action="save_print")
        self.client.post(self.url, data)
        self.client.post(self.url, data)

        self.assertEqual(Invoice.objects.count(), 1)
        self.assertEqual(Invoice.objects.get().print_count, 1)
        self.assertEqual(PrintJob.objects.count(), 1)

    def test_a_repeat_does_not_duplicate_the_customer(self):
        data = invoice_post_data(form_token=self._form_token())
        self.client.post(self.url, data)
        self.client.post(self.url, data)
        self.assertEqual(Customer.objects.count(), 1)

    def test_two_genuinely_different_bills_are_both_saved(self):
        self.client.post(self.url, invoice_post_data(form_token=self._form_token()))
        self.client.post(
            self.url,
            invoice_post_data(form_token=self._form_token(), customer_phone="9820055667"),
        )
        self.assertEqual(Invoice.objects.count(), 2)

    def test_the_same_customer_can_be_billed_twice_with_fresh_forms(self):
        self.client.post(self.url, invoice_post_data(form_token=self._form_token()))
        self.client.post(self.url, invoice_post_data(form_token=self._form_token()))
        self.assertEqual(Invoice.objects.count(), 2)
        self.assertEqual(Customer.objects.count(), 1)

    def test_a_missing_token_still_saves(self):
        # Older bookmarked forms or JS-less clients must not be locked out.
        data = invoice_post_data()
        data.pop("form_token", None)
        self.client.post(self.url, data)
        self.assertEqual(Invoice.objects.count(), 1)
        self.assertIsNone(Invoice.objects.get().form_token)

    def test_the_token_is_stored_on_the_bill(self):
        token = self._form_token()
        self.client.post(self.url, invoice_post_data(form_token=token))
        self.assertEqual(Invoice.objects.get().form_token, token)

    def test_editing_a_bill_is_unaffected(self):
        self.client.post(self.url, invoice_post_data(form_token=self._form_token()))
        invoice = Invoice.objects.get()
        item = invoice.items.get()

        edit_data = invoice_post_data(**{
            "items-INITIAL_FORMS": "1",
            "items-0-id": str(item.pk),
            "items-0-quantity": "1",
            "items-0-rate": "999",
        })
        # Editing twice must update, never duplicate.
        for _ in range(2):
            self.client.post(reverse("billing:invoice_update", args=[invoice.pk]), edit_data)

        self.assertEqual(Invoice.objects.count(), 1)
        invoice.refresh_from_db()
        self.assertEqual(invoice.grand_total, Decimal("999.00"))

    def test_the_form_page_is_not_cacheable(self):
        response = self.client.get(self.url)
        self.assertIn("no-store", response["Cache-Control"])
