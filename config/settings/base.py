"""
Base settings shared by every environment.

Environment-specific overrides live in dev.py / prod.py.
Secrets and machine-specific values come from the .env file.
"""
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    USE_SQLITE=(bool, False),
    ALLOWED_HOSTS=(list, []),
    CSRF_TRUSTED_ORIGINS=(list, []),
    EMAIL_PORT=(int, 587),
    EMAIL_USE_TLS=(bool, True),
    PRINT_COPIES=(int, 1),
    SECURE_SSL_REDIRECT=(bool, False),
    SESSION_COOKIE_SECURE=(bool, False),
    CSRF_COOKIE_SECURE=(bool, False),
)

# Read .env when present, so real environment variables still win in production.
_env_file = BASE_DIR / ".env"
if _env_file.exists():
    env.read_env(str(_env_file))

SECRET_KEY = env("SECRET_KEY", default="unsafe-dev-key-change-me")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env("CSRF_TRUSTED_ORIGINS")

# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.humanize",
    "django.contrib.staticfiles",
    "accounts",
    "billing",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "billing.context_processors.shop_context",
            ],
        },
    },
]

# ---------------------------------------------------------------------------
# Database (PostgreSQL, managed through pgAdmin)
# ---------------------------------------------------------------------------
if env("USE_SQLITE"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }
else:
    DATABASES = {
        "default": {
            **env.db(
                "DATABASE_URL",
                default="postgres://postgres:postgres@127.0.0.1:5432/shop_billing",
            ),
            "CONN_MAX_AGE": 60,
            "OPTIONS": {"connect_timeout": 10},
        }
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "billing:invoice_list"
LOGOUT_REDIRECT_URL = "accounts:login"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 8},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

PASSWORD_RESET_TIMEOUT = 60 * 60 * 2

# Brute-force protection for the login form (accounts.views.ThrottledLoginView).
LOGIN_MAX_ATTEMPTS = 8
LOGIN_LOCKOUT_SECONDS = 15 * 60

# ---------------------------------------------------------------------------
# Locale
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "en-in"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Static and media files
# ---------------------------------------------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}

# ---------------------------------------------------------------------------
# Email (drives the forgot-password flow)
# ---------------------------------------------------------------------------
EMAIL_BACKEND = env(
    "EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend"
)
EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_PORT = env("EMAIL_PORT")
EMAIL_USE_TLS = env("EMAIL_USE_TLS")
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="no-reply@shopbilling.local")

# ---------------------------------------------------------------------------
# Business rules
# ---------------------------------------------------------------------------
DEFAULT_CITY = env("DEFAULT_CITY", default="Himmatnagar")
CURRENCY_SYMBOL = env("CURRENCY_SYMBOL", default="Rs.")
INVOICE_NUMBER_PREFIX = env("INVOICE_NUMBER_PREFIX", default="INV")
MAX_LINE_ITEMS = 200

# ---------------------------------------------------------------------------
# Document pipeline:  Word template -> filled .docx -> PDF -> printer
# ---------------------------------------------------------------------------
# "docx" = fill the Word template and convert it (the real bill format).
# "html" = xhtml2pdf fallback, used when no Word template / Word is present.
DOCUMENT_ENGINE = env("DOCUMENT_ENGINE", default="docx")

# The static Word template with {{ placeholders }} supplied by the shop owner.
DOCX_TEMPLATE_PATH = Path(
    env("DOCX_TEMPLATE_PATH", default=str(BASE_DIR / "doc_templates" / "invoice_template.docx"))
)

# Where finished bills are archived. Point this anywhere, e.g. D:/Shop/Bills
INVOICE_PDF_DIR = Path(env("INVOICE_PDF_DIR", default=str(BASE_DIR / "media" / "invoices")))
# Filled .docx files (intermediate). Kept only when DOCX_KEEP_INTERMEDIATE=True.
INVOICE_DOCX_DIR = Path(env("INVOICE_DOCX_DIR", default=str(BASE_DIR / "media" / "invoices_docx")))
DOCX_KEEP_INTERMEDIATE = env.bool("DOCX_KEEP_INTERMEDIATE", default=True)
# How the archive folder is split up:
#   date  -> one folder per day, 2026-08-23/   (default, auto-created)
#   month -> 2026-08/
#   year  -> financial year, 2026-27/
#   none  -> everything in one flat folder
INVOICE_ARCHIVE_SUBFOLDER = env("INVOICE_ARCHIVE_SUBFOLDER", default="date")

# DOCX -> PDF converter:  auto | word | libreoffice
#   word        - MS Word via COM automation (best fidelity, needs Word installed)
#   libreoffice - soffice --headless (no Word needed)
DOCX_TO_PDF_CONVERTER = env("DOCX_TO_PDF_CONVERTER", default="auto")
LIBREOFFICE_PATH = env("LIBREOFFICE_PATH", default="") or None

# ---------------------------------------------------------------------------
# Printing
# ---------------------------------------------------------------------------
PRINT_BACKEND = env("PRINT_BACKEND", default="dummy")
PRINTER_NAME = env("PRINTER_NAME", default="") or None
PRINT_COPIES = env("PRINT_COPIES")
PRINT_SPOOL_DIR = MEDIA_ROOT / "print_spool"
SUMATRA_PDF_PATH = env("SUMATRA_PDF_PATH", default="") or None

ESCPOS_CONNECTION = env("ESCPOS_CONNECTION", default="network")
ESCPOS_HOST = env("ESCPOS_HOST", default="127.0.0.1")
ESCPOS_PORT = env.int("ESCPOS_PORT", default=9100)
ESCPOS_USB_VENDOR_ID = env("ESCPOS_USB_VENDOR_ID", default="0x04b8")
ESCPOS_USB_PRODUCT_ID = env("ESCPOS_USB_PRODUCT_ID", default="0x0e15")
ESCPOS_DEVICE_FILE = env("ESCPOS_DEVICE_FILE", default="/dev/usb/lp0")

# ---------------------------------------------------------------------------
# Security defaults (tightened further in prod.py)
# ---------------------------------------------------------------------------
X_FRAME_OPTIONS = "SAMEORIGIN"  # the PDF preview is shown in an iframe
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 60 * 60 * 12  # one shop shift

MESSAGE_STORAGE = "django.contrib.messages.storage.session.SessionStorage"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{asctime} [{levelname}] {name}: {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "verbose"},
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(LOG_DIR / "app.log"),
            "maxBytes": 5 * 1024 * 1024,
            "backupCount": 5,
            "formatter": "verbose",
            "encoding": "utf-8",
        },
    },
    "root": {"handlers": ["console", "file"], "level": "INFO"},
    "loggers": {
        "django.db.backends": {
            "level": "WARNING",
            "handlers": ["console"],
            "propagate": False,
        },
        "billing": {
            "level": "DEBUG" if DEBUG else "INFO",
            "handlers": ["console", "file"],
            "propagate": False,
        },
        "accounts": {
            "level": "INFO",
            "handlers": ["console", "file"],
            "propagate": False,
        },
    },
}
