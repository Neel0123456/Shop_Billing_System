"""Template context available on every page."""
from django.conf import settings


def shop_context(request):
    """
    Shop identity and pipeline info for the base template.

    The ShopProfile row is loaded lazily (only when a template actually touches
    `shop`), so anonymous pages such as the login screen do not hit the DB.
    """
    from .models import ShopProfile

    class LazyShop:
        _value = None

        def __getattr__(self, item):
            if LazyShop._value is None:
                LazyShop._value = ShopProfile.load()
            return getattr(LazyShop._value, item)

        def __str__(self):
            return str(getattr(self, "name", ""))

    return {
        "shop": LazyShop(),
        "currency": settings.CURRENCY_SYMBOL,
        "default_city": settings.DEFAULT_CITY,
        "print_backend": settings.PRINT_BACKEND,
        "document_engine": settings.DOCUMENT_ENGINE,
        "app_version": "1.0.0",
    }
