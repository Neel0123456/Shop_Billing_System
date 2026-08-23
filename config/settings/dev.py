"""Local development settings."""
from .base import *  # noqa: F401,F403
from .base import env  # noqa: F401

DEBUG = True
ALLOWED_HOSTS = ["127.0.0.1", "localhost", "0.0.0.0", "[::1]"]

# No hashed-manifest static files while developing.
STORAGES["staticfiles"] = {  # noqa: F405
    "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
}

# Speeds up the test suite considerably.
if env.bool("FAST_HASHING", default=False):
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

INTERNAL_IPS = ["127.0.0.1"]
