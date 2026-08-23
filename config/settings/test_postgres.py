"""
Same as the test settings, but against real PostgreSQL.

    python manage.py test --settings=config.settings.test_postgres

Worth running before a release: SQLite is forgiving about things PostgreSQL
is not (SELECT ... FOR UPDATE, strict types, __date lookups with timezones).
"""
from .test import *  # noqa: F401,F403
from .base import env

DATABASES = {
    "default": {
        **env.db("DATABASE_URL", default="postgres://postgres:postgres@127.0.0.1:5432/shop_billing"),
    }
}
