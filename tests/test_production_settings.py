"""
Tests for production settings hardening.

Validator tests import config.settings._validators directly — no prod-only packages needed.
The auth-class test reloads config.settings.production with dj_database_url mocked out,
since that package is only installed in the production venv.
"""
import importlib
import sys
import os
import pytest
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Validator unit tests (no prod dependencies required)
# ---------------------------------------------------------------------------

class TestRequireSecretKey:
    def test_valid_key_returned(self):
        from config.settings._validators import require_secret_key
        with patch.dict(os.environ, {'SECRET_KEY': 'a-long-secure-key'}):
            assert require_secret_key() == 'a-long-secure-key'

    def test_empty_raises(self):
        from django.core.exceptions import ImproperlyConfigured
        from config.settings._validators import require_secret_key
        with patch.dict(os.environ, {'SECRET_KEY': ''}):
            with pytest.raises(ImproperlyConfigured, match='SECRET_KEY'):
                require_secret_key()

    def test_insecure_default_prefix_raises(self):
        from django.core.exceptions import ImproperlyConfigured
        from config.settings._validators import require_secret_key
        with patch.dict(os.environ, {'SECRET_KEY': 'django-insecure-change-me-in-production'}):
            with pytest.raises(ImproperlyConfigured, match='SECRET_KEY'):
                require_secret_key()

    def test_any_django_insecure_prefix_raises(self):
        from django.core.exceptions import ImproperlyConfigured
        from config.settings._validators import require_secret_key
        with patch.dict(os.environ, {'SECRET_KEY': 'django-insecure-somethingelse'}):
            with pytest.raises(ImproperlyConfigured, match='SECRET_KEY'):
                require_secret_key()

    def test_whitespace_only_raises(self):
        from django.core.exceptions import ImproperlyConfigured
        from config.settings._validators import require_secret_key
        with patch.dict(os.environ, {'SECRET_KEY': '   '}):
            with pytest.raises(ImproperlyConfigured, match='SECRET_KEY'):
                require_secret_key()


class TestRequireAllowedHosts:
    def test_single_host_returned_as_list(self):
        from config.settings._validators import require_allowed_hosts
        with patch.dict(os.environ, {'ALLOWED_HOSTS': 'api.example.com'}):
            assert require_allowed_hosts() == ['api.example.com']

    def test_multiple_hosts_parsed(self):
        from config.settings._validators import require_allowed_hosts
        with patch.dict(os.environ, {'ALLOWED_HOSTS': 'api.example.com,www.example.com'}):
            result = require_allowed_hosts()
            assert 'api.example.com' in result
            assert 'www.example.com' in result

    def test_hosts_are_stripped(self):
        from config.settings._validators import require_allowed_hosts
        with patch.dict(os.environ, {'ALLOWED_HOSTS': ' api.example.com , www.example.com '}):
            result = require_allowed_hosts()
            assert 'api.example.com' in result
            assert 'www.example.com' in result

    def test_empty_raises(self):
        from django.core.exceptions import ImproperlyConfigured
        from config.settings._validators import require_allowed_hosts
        with patch.dict(os.environ, {'ALLOWED_HOSTS': ''}):
            with pytest.raises(ImproperlyConfigured, match='ALLOWED_HOSTS'):
                require_allowed_hosts()

    def test_whitespace_only_raises(self):
        from django.core.exceptions import ImproperlyConfigured
        from config.settings._validators import require_allowed_hosts
        with patch.dict(os.environ, {'ALLOWED_HOSTS': '   '}):
            with pytest.raises(ImproperlyConfigured, match='ALLOWED_HOSTS'):
                require_allowed_hosts()


# ---------------------------------------------------------------------------
# Auth class test — reloads production.py with dj_database_url mocked
# ---------------------------------------------------------------------------

_VALID_PROD_ENV = {
    'SECRET_KEY': 'a-very-long-and-secure-random-secret-key-for-testing-only',
    'ALLOWED_HOSTS': 'api.example.com',
}


def _load_production_settings():
    """Reload production settings with dj_database_url stubbed out."""
    for key in [k for k in sys.modules if k.startswith('config.settings')]:
        del sys.modules[key]
    stub = MagicMock()
    stub.config.return_value = {}
    with patch.dict(sys.modules, {'dj_database_url': stub}):
        with patch.dict(os.environ, _VALID_PROD_ENV):
            return importlib.import_module('config.settings.production')


@pytest.fixture(autouse=True)
def _restore_settings_modules():
    yield
    for key in [k for k in sys.modules if k.startswith('config.settings')]:
        del sys.modules[key]


class TestProductionAuthClasses:
    def test_firebase_is_first_auth_class(self):
        mod = _load_production_settings()
        classes = mod.REST_FRAMEWORK['DEFAULT_AUTHENTICATION_CLASSES']
        assert classes[0] == 'apps.accounts.authentication.FirebaseAuthentication'

    def test_token_auth_preserved(self):
        mod = _load_production_settings()
        classes = mod.REST_FRAMEWORK['DEFAULT_AUTHENTICATION_CLASSES']
        assert 'rest_framework.authentication.TokenAuthentication' in classes

    def test_session_auth_preserved(self):
        mod = _load_production_settings()
        classes = mod.REST_FRAMEWORK['DEFAULT_AUTHENTICATION_CLASSES']
        assert 'rest_framework.authentication.SessionAuthentication' in classes

    def test_exactly_three_auth_classes(self):
        mod = _load_production_settings()
        classes = mod.REST_FRAMEWORK['DEFAULT_AUTHENTICATION_CLASSES']
        assert len(classes) == 3
