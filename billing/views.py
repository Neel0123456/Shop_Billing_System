"""
Billing views.

Two buttons drive the whole app:

  Save              -> validate, store in PostgreSQL, archive the PDF on disk
  Save and Print    -> the same, then hand the PDF straight to the printer

Both paths go through `billing.services.documents`, so the Word-template
pipeline and the printer backend can change without touching this module.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import IntegrityError, transaction
from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from django.views import View
from django.views.generic import DetailView, ListView, UpdateView

from .forms import (
    InvoiceFilterForm,
    InvoiceForm,
    InvoiceLineItemFormSet,
    RecordPaymentForm,
    ShopProfileForm,
)
from .models import Customer, Invoice, PaymentStatus, PrintJob, ShopProfile, money
from .services import documents
from .services.context import PLACEHOLDER_GROUPS

logger = logging.getLogger(__name__)
ZERO = Decimal("0.00")

DEC = DecimalField(max_digits=14, decimal_places=2)


def _zero_sum(field):
    """Sum() that returns 0 instead of None on an empty queryset."""
    return Coalesce(Sum(field), Value(ZERO), output_field=DEC)


# ===========================================================================
# Listing
# ===========================================================================
class InvoiceListView(LoginRequiredMixin, ListView):
    """All bills, with the filters the shop asked for and running totals."""

    model = Invoice
    template_name = "billing/invoice_list.html"
    context_object_name = "invoices"
    paginate_by = 25

    def get_queryset(self):
        qs = Invoice.objects.with_related()
        self.filter_form = InvoiceFilterForm(self.request.GET or None)

        if not (self.request.GET and self.filter_form.is_valid()):
            return qs

        data = self.filter_form.cleaned_data

        if term := data.get("q"):
            qs = qs.filter(
                Q(number__icontains=term)
                | Q(customer_name__icontains=term)
                | Q(customer_phone__icontains=term)
                | Q(customer_email__icontains=term)
                | Q(customer_city__icontains=term)
            )
        if value := data.get("name"):
            qs = qs.filter(customer_name__icontains=value)
        if value := data.get("phone"):
            digits = "".join(ch for ch in value if ch.isdigit())
            qs = qs.filter(customer_phone__icontains=digits or value)
        if value := data.get("email"):
            qs = qs.filter(customer_email__icontains=value)
        if value := data.get("city"):
            qs = qs.filter(customer_city__icontains=value)

        payment = data.get("payment")
        if payment == InvoiceFilterForm.OUTSTANDING:
            qs = qs.outstanding()
        elif payment == "OVERDUE":
            qs = qs.overdue()
        elif payment:
            qs = qs.filter(payment_status=payment)

        if value := data.get("date_from"):
            qs = qs.filter(invoice_date__gte=value)
        if value := data.get("date_to"):
            qs = qs.filter(invoice_date__lte=value)
        if value := data.get("created_on"):
            # created_at is a timestamp, so compare on its local date part.
            qs = qs.filter(created_at__date=value)
        if (value := data.get("min_amount")) is not None:
            qs = qs.filter(grand_total__gte=value)
        if (value := data.get("max_amount")) is not None:
            qs = qs.filter(grand_total__lte=value)

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        qs = self.get_queryset()

        totals = qs.aggregate(
            count=Count("id"),
            billed=_zero_sum("grand_total"),
            received=_zero_sum("paid_amount"),
        )
        outstanding = qs.outstanding().aggregate(
            count=Count("id"), amount=_zero_sum("balance")
        )
        overpaid = qs.filter(payment_status=PaymentStatus.OVERPAID).aggregate(
            amount=_zero_sum("balance")
        )
        today = timezone.localdate()

        ctx.update(
            {
                "filter_form": self.filter_form,
                "summary": {
                    "count": totals["count"],
                    "billed": totals["billed"],
                    "received": totals["received"],
                    "pending": outstanding["amount"],
                    "pending_count": outstanding["count"],
                    "overpaid": abs(overpaid["amount"] or ZERO),
                },
                "querystring": self._querystring_without_page(),
                "active_filters": self._active_filters(),
                "today": today,
                "yesterday": today - timedelta(days=1),
                "month_start": today.replace(day=1),
                "is_filtered": bool(self.request.GET),
            }
        )
        return ctx

    def _querystring_without_page(self) -> str:
        params = self.request.GET.copy()
        params.pop("page", None)
        encoded = params.urlencode()
        return f"&{encoded}" if encoded else ""

    def _active_filters(self) -> list[dict]:
        """
        One chip per filter in use, each with a link that drops just that one.

        Makes it obvious why the list is short, instead of leaving the operator
        hunting through the form for the field they typed in earlier.
        """
        if not self.filter_form.is_bound or not self.filter_form.is_valid():
            return []

        payment_labels = dict(InvoiceFilterForm.PAYMENT_CHOICES)
        chips = []

        for name, value in self.filter_form.cleaned_data.items():
            if value in (None, "", []):
                continue

            if name == "payment":
                shown = payment_labels.get(value, value)
            elif hasattr(value, "strftime"):
                shown = value.strftime("%d %b %Y")
            else:
                shown = value

            remaining = self.request.GET.copy()
            remaining.pop(name, None)
            remaining.pop("page", None)
            encoded = remaining.urlencode()

            chips.append(
                {
                    "field": name,
                    "label": self.filter_form.fields[name].label,
                    "value": shown,
                    "remove_url": f"?{encoded}" if encoded else self.request.path,
                }
            )
        return chips


# ===========================================================================
# Create / edit
# ===========================================================================
class InvoiceFormMixin:
    """
    Shared save logic for creating and editing a bill.

    The header form needs the line-item total before it can validate the
    payment state, so the formset is cleaned first and its total is injected
    into the header form. Everything then commits inside one transaction, so a
    half-written bill can never reach the database.
    """

    template_name = "billing/invoice_form.html"

    def get_object_or_none(self):
        return None

    def _build_forms(self, data=None):
        instance = self.get_object_or_none()
        form = InvoiceForm(data, instance=instance)
        formset = InvoiceLineItemFormSet(data, instance=instance)
        return form, formset

    def get(self, request, *args, **kwargs):
        form, formset = self._build_forms()
        return self.render_page(form, formset)

    def _already_saved(self, request):
        """
        The bill this exact form submission already created, if any.

        Guards against every flavour of double submission: a double click, the
        browser Back button followed by Save again, or a retried request. All of
        them carry the same form_token, so the second attempt is recognised
        instead of producing another bill (and another amount in the totals).
        """
        if self.get_object_or_none():
            return None  # editing updates one row, it cannot duplicate
        token = (request.POST.get("form_token") or "").strip()
        if not token:
            return None
        return Invoice.objects.filter(form_token=token).first()

    def post(self, request, *args, **kwargs):
        duplicate = self._already_saved(request)
        if duplicate:
            messages.info(
                request,
                f"This bill was already saved as {duplicate.number}. "
                f"Nothing was added a second time.",
            )
            return redirect(duplicate.get_absolute_url())

        form, formset = self._build_forms(request.POST)

        # Validate the lines first so the header knows the bill total.
        formset_ok = formset.is_valid()
        if formset_ok:
            form.computed_total = formset.computed_totals()["grand_total"]
        form_ok = form.is_valid()

        if not (form_ok and formset_ok):
            messages.error(request, "Please correct the highlighted fields.")
            return self.render_page(form, formset)

        try:
            invoice = self.save_invoice(form, formset)
        except IntegrityError:
            # Two requests raced past the check above; the unique constraint on
            # form_token stopped the second one. Show the bill that survived.
            existing = self._already_saved(request)
            if existing:
                messages.info(request, f"This bill was already saved as {existing.number}.")
                return redirect(existing.get_absolute_url())
            raise

        action = request.POST.get("action", "save")
        if action == "save_print":
            return self.save_and_print(invoice)

        messages.success(
            request,
            f"Bill {invoice.number} saved for {invoice.customer_name} "
            f"({documents.active_engine()} engine).",
        )
        return redirect(invoice.get_absolute_url())

    @transaction.atomic
    def save_invoice(self, form, formset) -> Invoice:
        customer = form.sync_customer()

        invoice: Invoice = form.save(commit=False)
        invoice.customer = customer
        if not invoice.pk:
            invoice.created_by = self.request.user
            # Stamp the submission key so a repeat of this POST is recognised.
            invoice.form_token = (form.cleaned_data.get("form_token") or "").strip() or None
        invoice.save()

        formset.instance = invoice
        items = formset.save(commit=False)

        for obj in formset.deleted_objects:
            obj.delete()

        # Renumber the surviving rows so the printed bill is in screen order.
        for position, item in enumerate(items, start=1):
            item.invoice = invoice
            item.position = position
            item.save()
        for position, item in enumerate(
            invoice.items.exclude(pk__in=[i.pk for i in items if i.pk]), start=len(items) + 1
        ):
            if item.position != position:
                item.position = position
                item.save(update_fields=["position"])

        invoice.recalculate(commit=False)
        invoice.apply_payment_status()
        invoice.save()

        # Keep an archived PDF on the shop PC for every saved bill.
        try:
            documents.archive_pdf(invoice, printed_by=self.request.user)
        except Exception as exc:  # a failed archive must not lose the sale
            logger.error("Could not archive the PDF for %s: %s", invoice.number, exc)
            messages.warning(
                self.request,
                f"The bill was saved, but the PDF could not be written to disk: {exc}",
            )
        return invoice

    def save_and_print(self, invoice: Invoice):
        job = documents.print_invoice(invoice, requested_by=self.request.user)

        if getattr(job, "needs_browser_dialog", False):
            messages.info(self.request, f"Bill {invoice.number} saved. Opening the print dialog.")
            return redirect(reverse("billing:invoice_browser_print", args=[invoice.pk]))

        if job.succeeded:
            messages.success(self.request, f"Bill {invoice.number} saved. {job.message}")
        else:
            messages.error(
                self.request,
                f"Bill {invoice.number} was saved, but printing failed: {job.message}",
            )
        return redirect(invoice.get_absolute_url())

    def render_page(self, form, formset):
        response = render(
            self.request,
            self.template_name,
            {
                "form": form,
                "formset": formset,
                "invoice": self.get_object_or_none(),
                "is_edit": bool(self.get_object_or_none()),
                "payment_statuses": PaymentStatus,
                "engine": documents.active_engine(),
            },
        )
        # Never serve the bill form from the browser cache. Pressing Back after
        # a save must fetch a fresh form with a fresh token, not re-show the
        # filled one and invite another submission.
        response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response["Pragma"] = "no-cache"
        return response


class InvoiceCreateView(LoginRequiredMixin, InvoiceFormMixin, View):
    """New bill."""

    def get_object_or_none(self):
        return None


class InvoiceUpdateView(LoginRequiredMixin, InvoiceFormMixin, View):
    """Edit an existing bill, reusing every rule from the create path."""

    def get_object_or_none(self):
        if not hasattr(self, "_object"):
            self._object = get_object_or_404(Invoice, pk=self.kwargs["pk"])
        return self._object


# ===========================================================================
# Detail
# ===========================================================================
class InvoiceDetailView(LoginRequiredMixin, DetailView):
    model = Invoice
    template_name = "billing/invoice_detail.html"
    context_object_name = "invoice"

    def get_queryset(self):
        return Invoice.objects.with_related()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["items"] = self.object.items.all()
        ctx["print_jobs"] = self.object.print_jobs.select_related("requested_by")[:10]
        ctx["payment_form"] = RecordPaymentForm(invoice=self.object)
        ctx["shop"] = ShopProfile.load()
        return ctx


# ===========================================================================
# PDF / print actions
# ===========================================================================
@login_required
@require_GET
def invoice_pdf(request, pk):
    """Serve the bill PDF. `?download=1` forces the Save-As dialog."""
    invoice = get_object_or_404(Invoice, pk=pk)
    try:
        pdf_bytes = documents.build_pdf(invoice, printed_by=request.user)
    except Exception as exc:
        logger.exception("PDF generation failed for %s", invoice.number)
        messages.error(request, f"Could not generate the PDF: {exc}")
        return redirect(invoice.get_absolute_url())

    disposition = "attachment" if request.GET.get("download") else "inline"
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'{disposition}; filename="{invoice.pdf_filename}"'
    response["Content-Length"] = str(len(pdf_bytes))
    return response


@login_required
@require_POST
def invoice_print(request, pk):
    """Reprint an existing bill from the listing or the detail page."""
    invoice = get_object_or_404(Invoice, pk=pk)
    copies = request.POST.get("copies")
    job = documents.print_invoice(
        invoice,
        requested_by=request.user,
        copies=int(copies) if copies and copies.isdigit() else None,
    )

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse(
            {
                "ok": job.succeeded,
                "status": job.status,
                "message": job.message,
                "print_count": invoice.print_count,
                "browser_dialog": getattr(job, "needs_browser_dialog", False),
                "preview_url": reverse("billing:invoice_browser_print", args=[invoice.pk]),
            },
            status=200 if job.succeeded else 502,
        )

    if getattr(job, "needs_browser_dialog", False):
        return redirect(reverse("billing:invoice_browser_print", args=[invoice.pk]))

    if job.succeeded:
        messages.success(request, job.message)
    else:
        messages.error(request, f"Printing failed: {job.message}")
    return redirect(request.POST.get("next") or invoice.get_absolute_url())


@login_required
@require_GET
def invoice_browser_print(request, pk):
    """
    Page that opens the browser print dialog on the embedded PDF.

    Only used by the `browser` backend, or manually when someone wants to
    print to a different printer than the configured one.
    """
    invoice = get_object_or_404(Invoice, pk=pk)
    return render(
        request,
        "billing/invoice_browser_print.html",
        {"invoice": invoice, "autoprint": request.GET.get("auto", "1") == "1"},
    )


@login_required
@require_GET
def invoice_preview(request, pk):
    """On-screen HTML preview of the bill (always available, no Word needed)."""
    from .services import html_engine

    invoice = get_object_or_404(Invoice, pk=pk)
    return HttpResponse(html_engine.render_html(invoice, printed_by=request.user, for_screen=True))


@login_required
@require_POST
def invoice_record_payment(request, pk):
    """Add a payment to an outstanding bill and re-derive its status."""
    invoice = get_object_or_404(Invoice, pk=pk)
    form = RecordPaymentForm(request.POST, invoice=invoice)

    if not form.is_valid():
        for error in form.errors.values():
            messages.error(request, "; ".join(error))
        return redirect(invoice.get_absolute_url())

    amount = money(form.cleaned_data["amount"])
    invoice.paid_amount = money(invoice.paid_amount + amount)

    if invoice.paid_amount > invoice.grand_total:
        invoice.payment_status = PaymentStatus.OVERPAID
        invoice.due_date = None
    elif invoice.paid_amount == invoice.grand_total:
        invoice.payment_status = PaymentStatus.PAID
        invoice.due_date = None
    else:
        invoice.payment_status = PaymentStatus.UNDERPAID
        invoice.due_date = form.cleaned_data.get("new_due_date") or invoice.due_date

    invoice.balance = money(invoice.grand_total - invoice.paid_amount)
    invoice.save(update_fields=["paid_amount", "payment_status", "due_date", "balance", "updated_at"])

    messages.success(
        request,
        f"Recorded {amount} against {invoice.number}. "
        f"Status is now {invoice.get_payment_status_display().lower()}.",
    )
    return redirect(invoice.get_absolute_url())


@login_required
@require_POST
def invoice_delete(request, pk):
    """Delete a bill. Kept deliberately explicit: POST only, with a confirm."""
    invoice = get_object_or_404(Invoice, pk=pk)
    number = invoice.number
    invoice.delete()
    messages.success(request, f"Bill {number} was deleted.")
    return redirect("billing:invoice_list")


# ===========================================================================
# Customer lookup (speeds up repeat customers at the counter)
# ===========================================================================
@login_required
@require_GET
def customer_lookup(request):
    """Return a saved customer for a phone number, for auto-filling the form."""
    phone = "".join(ch for ch in request.GET.get("phone", "") if ch.isdigit())
    if len(phone) < 7:
        return JsonResponse({"found": False})

    customer = Customer.objects.filter(phone__icontains=phone).order_by("-updated_at").first()
    if not customer:
        return JsonResponse({"found": False})

    return JsonResponse(
        {
            "found": True,
            "name": customer.name,
            "email": customer.email,
            "city": customer.city,
            "invoice_count": customer.invoices.count(),
        }
    )


# ===========================================================================
# Settings pages
# ===========================================================================
class ShopProfileView(LoginRequiredMixin, UpdateView):
    """Edit the letterhead that goes on every bill."""

    model = ShopProfile
    form_class = ShopProfileForm
    template_name = "billing/shop_profile.html"

    def get_object(self, queryset=None):
        return ShopProfile.load()

    def get_success_url(self):
        messages.success(self.request, "Shop details saved.")
        return reverse("billing:shop_profile")


@login_required
@require_GET
def print_settings(request):
    """
    Diagnostics page: which engine, which converter, which printer.

    This is the page to open when a print does not come out, before touching
    any code.
    """
    return render(
        request,
        "billing/print_settings.html",
        {
            "status": documents.pipeline_status(),
            "placeholders": PLACEHOLDER_GROUPS,
            "recent_jobs": PrintJob.objects.select_related("invoice", "requested_by")[:20],
        },
    )


@login_required
@require_POST
def print_test_page(request):
    """Send the most recent bill to the printer as a hardware test."""
    invoice = Invoice.objects.order_by("-id").first()
    if not invoice:
        messages.warning(request, "Create a bill first, then run the printer test.")
        return redirect("billing:print_settings")

    job = documents.print_invoice(invoice, requested_by=request.user, archive=False)
    if job.succeeded:
        messages.success(request, f"Test print sent. {job.message}")
    else:
        messages.error(request, f"Test print failed: {job.message}")
    return redirect("billing:print_settings")
