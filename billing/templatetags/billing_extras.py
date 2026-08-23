"""Template filters for money and status rendering."""
from django import template
from django.conf import settings
from django.utils.safestring import mark_safe

from billing.utils import amount_in_words, format_inr

register = template.Library()


@register.filter(name="inr")
def inr(value):
    """1234567.5 -> '12,34,567.50' (Indian digit grouping)."""
    return format_inr(value)


@register.filter(name="money")
def money_with_symbol(value):
    """Same as `inr`, prefixed with the configured currency symbol."""
    return format_inr(value, symbol=settings.CURRENCY_SYMBOL)


@register.filter(name="in_words")
def in_words(value):
    return amount_in_words(value)


@register.simple_tag
def status_badge(invoice):
    """Coloured badge for a bill's payment state."""
    label = invoice.get_payment_status_display()
    css = invoice.status_css
    if invoice.is_overdue:
        label = f"{label} - overdue"
        css = "danger"
    return mark_safe(f'<span class="badge badge-{css}">{label}</span>')


@register.simple_tag(takes_context=True)
def querystring_replace(context, **kwargs):
    """Rebuild the current query string with some parameters replaced."""
    params = context["request"].GET.copy()
    for key, value in kwargs.items():
        if value in (None, ""):
            params.pop(key, None)
        else:
            params[key] = value
    encoded = params.urlencode()
    return f"?{encoded}" if encoded else ""
