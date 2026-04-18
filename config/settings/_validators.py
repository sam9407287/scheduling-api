"""
Fail-fast validators for production settings.
Extracted so tests can verify them without importing the full production module
(which pulls in production-only packages like dj_database_url).
"""
import os
from django.core.exceptions import ImproperlyConfigured


def require_secret_key() -> str:
    value = os.environ.get('SECRET_KEY', '').strip()
    if not value or value.startswith('django-insecure-'):
        raise ImproperlyConfigured(
            "SECRET_KEY must be set to a secure random value in production. "
            "Set the SECRET_KEY environment variable."
        )
    return value


def require_allowed_hosts() -> list:
    raw = os.environ.get('ALLOWED_HOSTS', '').strip()
    if not raw:
        raise ImproperlyConfigured(
            "ALLOWED_HOSTS must be set in production. "
            "Set the ALLOWED_HOSTS environment variable to a comma-separated list of allowed hosts."
        )
    return [h.strip() for h in raw.split(',') if h.strip()]
