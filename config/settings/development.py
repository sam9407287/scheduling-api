"""
Development settings
"""
from .base import *

DEBUG = True

# Optionally install debug toolbar and django-extensions
try:
    import debug_toolbar  # noqa
    INSTALLED_APPS += ['debug_toolbar']
    MIDDLEWARE += ['debug_toolbar.middleware.DebugToolbarMiddleware']
except ImportError:
    pass

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

# Email backend (console for development)
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

# Allow all hosts in development
ALLOWED_HOSTS = ['*']
