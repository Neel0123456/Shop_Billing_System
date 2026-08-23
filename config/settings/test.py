"""
Settings used by the test suite.

Hermetic on purpose: in-memory SQLite, no Word/printer dependency, fast
password hashing. `manage.py test` picks this up automatically via
`billing/tests/__init__.py`-free explicit invocation:

    python manage.py test --settings=config.settings.test
"""
from .base import *  # noqa: F401,F403
from .base import BASE_DIR

DEBUG = False
ALLOWED_HOSTS = ["*"]
SECRET_KEY = "test-secret-key-not-used-anywhere-else"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Never touch a real printer or Word during tests.
PRINT_BACKEND = "dummy"
DOCUMENT_ENGINE = "html"

MEDIA_ROOT = BASE_DIR / "media" / "test"
INVOICE_PDF_DIR = MEDIA_ROOT / "invoices"
INVOICE_DOCX_DIR = MEDIA_ROOT / "invoices_docx"
PRINT_SPOOL_DIR = MEDIA_ROOT / "print_spool"

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# WhiteNoise only warns about a missing collectstatic output during tests.
MIDDLEWARE = [m for m in MIDDLEWARE if "whitenoise" not in m]  # noqa: F405

STORAGES["staticfiles"] = {  # noqa: F405
    "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
}

# Keep the log files out of the test run.
LOGGING["root"]["handlers"] = ["console"]  # noqa: F405
for _logger in LOGGING["loggers"].values():  # noqa: F405
    _logger["handlers"] = ["console"]
    _logger["level"] = "CRITICAL"
