"""
Testing settings - uses SQLite for fast local testing
"""
from .base import *

DEBUG = True
SECRET_KEY = 'test-secret-key-not-for-production'

# Use SQLite for testing (no PostgreSQL needed)
import os
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': os.environ.get('TEST_DB_PATH', ':memory:'),
    }
}

# Faster password hashing for tests
PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.MD5PasswordHasher',
]

# Disable Celery during testing
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# Use session auth + token auth for testing (no Firebase needed)
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.TokenAuthentication',
        'rest_framework.authentication.SessionAuthentication',
        'rest_framework.authentication.BasicAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 20,
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
}

# Disable audit log middleware for testing
MIDDLEWARE = [m for m in MIDDLEWARE if 'AuditLog' not in m]

ALLOWED_HOSTS = ['*']

# Add authtoken to INSTALLED_APPS for Token auth
if 'rest_framework.authtoken' not in INSTALLED_APPS:
    INSTALLED_APPS += ['rest_framework.authtoken']

# Email backend
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'

# Disable audit signals during testing
AUDIT_DISABLED = True
