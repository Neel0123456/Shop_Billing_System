"""
Production settings.

Windows (the shop PC):
    set DJANGO_SETTINGS_MODULE=config.settings.prod
    python -m waitress --listen=0.0.0.0:8000 config.wsgi:application

Linux:
    gunicorn config.wsgi:application --workers 3 --bind 0.0.0.0:8000
"""
from .base import *  # noqa: F401,F403
from .base import LOGGING, env

DEBUG = False

# No fallbacks here on purpose: misconfiguration must fail at boot, not silently.
SECRET_KEY = env("SECRET_KEY")
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

# --- HTTPS / cookies -------------------------------------------------------
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
SESSION_COOKIE_SECURE = env.bool("SESSION_COOKIE_SECURE", default=True)
CSRF_COOKIE_SECURE = env.bool("CSRF_COOKIE_SECURE", default=True)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

SECURE_HSTS_SECONDS = 60 * 60 * 24 * 365
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

SESSION_COOKIE_NAME = "shopbilling_session"
CSRF_COOKIE_NAME = "shopbilling_csrf"

ADMINS = [("Shop owner", env("ADMIN_EMAIL", default="owner@example.com"))]
SERVER_EMAIL = env("DEFAULT_FROM_EMAIL", default="no-reply@shopbilling.local")

LOGGING["handlers"]["mail_admins"] = {
    "class": "django.utils.log.AdminEmailHandler",
    "level": "ERROR",
}
LOGGING["loggers"]["django.request"] = {
    "handlers": ["file", "mail_admins"],
    "level": "ERROR",
    "propagate": False,
}
