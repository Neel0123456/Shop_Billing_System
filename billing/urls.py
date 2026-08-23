from django.urls import path

from . import views

app_name = "billing"

urlpatterns = [
    # --- bills ---------------------------------------------------------
    path("", views.InvoiceListView.as_view(), name="invoice_list"),
    path("new/", views.InvoiceCreateView.as_view(), name="invoice_create"),
    path("<int:pk>/", views.InvoiceDetailView.as_view(), name="invoice_detail"),
    path("<int:pk>/edit/", views.InvoiceUpdateView.as_view(), name="invoice_update"),
    path("<int:pk>/delete/", views.invoice_delete, name="invoice_delete"),

    # --- documents -----------------------------------------------------
    path("<int:pk>/pdf/", views.invoice_pdf, name="invoice_pdf"),
    path("<int:pk>/preview/", views.invoice_preview, name="invoice_preview"),
    path("<int:pk>/print/", views.invoice_print, name="invoice_print"),
    path(
        "<int:pk>/print/browser/",
        views.invoice_browser_print,
        name="invoice_browser_print",
    ),

    # --- payments ------------------------------------------------------
    path("<int:pk>/payment/", views.invoice_record_payment, name="invoice_record_payment"),

    # --- helpers -------------------------------------------------------
    path("api/customer-lookup/", views.customer_lookup, name="customer_lookup"),

    # --- settings ------------------------------------------------------
    path("settings/shop/", views.ShopProfileView.as_view(), name="shop_profile"),
    path("settings/print/", views.print_settings, name="print_settings"),
    path("settings/print/test/", views.print_test_page, name="print_test"),
]
