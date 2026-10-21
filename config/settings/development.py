"""
Development settings
"""
import os
from .base import *

DEBUG = True

# debug_toolbar is intentionally NOT enabled here: its middleware tries to
# reverse the 'djdt' namespace (not registered in config/urls.py) and raises
# NoReverseMatch on JSON responses. Enable it only if you also wire up its URLs.

try:
    import django_extensions  # noqa
    INSTALLED_APPS += ['django_extensions']
except ImportError:
    pass

# Debug Toolbar
INTERNAL_IPS = [
    '127.0.0.1',
    'localhost',
]

# Email backend: console unless EMAIL_HOST_USER is set (then base.py picks SMTP,
# handy for testing real delivery locally). The register/send-code response
# echoes the code (`debug_code`) so the frontend can be wired without a mailbox.
if not os.getenv('EMAIL_HOST_USER'):
    EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
EMAIL_VERIFICATION_EXPOSE_CODE = True

# Allow all hosts in development
ALLOWED_HOSTS = ['*']

# ---------------------------------------------------------------------------
# Local dev: SQLite + Token auth (no PostgreSQL / Firebase needed)
# Lets the frontend log in via POST /api/auth/login/ with admin/admin123.
# ---------------------------------------------------------------------------

# SQLite so local dev does not require a running PostgreSQL.
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'dev_local.sqlite3',
    }
}

# Token model needs its app installed to create the auth_token table.
if 'rest_framework.authtoken' not in INSTALLED_APPS:
    INSTALLED_APPS += ['rest_framework.authtoken']

# Replace Firebase auth with Token/Session auth for local dev so the
# frontend's `Authorization: Token <key>` header authenticates.
REST_FRAMEWORK['DEFAULT_AUTHENTICATION_CLASSES'] = [
    'rest_framework.authentication.TokenAuthentication',
    'rest_framework.authentication.SessionAuthentication',
]
