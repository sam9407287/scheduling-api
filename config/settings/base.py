"""
Django settings for scheduling-api project.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Build paths inside the project
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.getenv('SECRET_KEY', 'django-insecure-change-me-in-production')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.getenv('DEBUG', '0') == '1'

ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    
    # Third party
    'rest_framework',
    'corsheaders',
    'drf_spectacular',
    
    # Local apps
    'apps.accounts',
    'apps.organizations',
    'apps.employees',
    'apps.shifts',
    'apps.schedules',
    'apps.attendance',
    'apps.overtime',
    'apps.compliance',
    'apps.ai_engine',
    'apps.audit',
    'apps.billing',
    'apps.leaves',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'apps.audit.middleware.AuditLogMiddleware',  # Custom audit logging
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# Database
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('DB_NAME', 'scheduling_db'),
        'USER': os.getenv('DB_USER', 'postgres'),
        'PASSWORD': os.getenv('DB_PASSWORD', 'postgres'),
        'HOST': os.getenv('DB_HOST', 'localhost'),
        'PORT': os.getenv('DB_PORT', '5432'),
    }
}

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Internationalization
LANGUAGE_CODE = 'zh-hant'
TIME_ZONE = 'Asia/Taipei'
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Media files
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Custom User Model
AUTH_USER_MODEL = 'accounts.User'

# REST Framework
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'apps.accounts.authentication.FirebaseAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 20,
    'DEFAULT_FILTER_BACKENDS': [
        'rest_framework.filters.SearchFilter',
        'rest_framework.filters.OrderingFilter',
    ],
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
}

# CORS
CORS_ALLOWED_ORIGINS = os.getenv(
    'CORS_ALLOWED_ORIGINS',
    'http://localhost:3000,http://localhost:5173'
).split(',')

CORS_ALLOW_CREDENTIALS = True

# API Documentation (drf-spectacular)
SPECTACULAR_SETTINGS = {
    'TITLE': 'AI 排班系統 API',
    'DESCRIPTION': 'AI 排班系統後端 REST API 文件',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
    'COMPONENT_SPLIT_REQUEST': True,
}

# Celery
CELERY_BROKER_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
CELERY_RESULT_BACKEND = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE

# Celery Beat — periodic tasks. The billing-threshold scan runs hourly.
CELERY_BEAT_SCHEDULE = {
    'scan-billing-thresholds': {
        'task': 'apps.billing.tasks.scan_billing_thresholds',
        'schedule': 3600.0,  # every hour
    },
}

# Email. Phase 3 uses the console backend (alerts are logged, not actually
# delivered). Phase 4 will switch EMAIL_BACKEND via env var to a real
# provider — no code change needed beyond the env var + credentials.
# 2026-10-22：自助註冊要真的寄驗證碼 → 設了 EMAIL_HOST_USER 就自動改走 SMTP
# （Gmail：smtp.gmail.com:587 + 應用程式密碼；收件人不限 Gmail）。
EMAIL_HOST = os.getenv('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', '587'))
EMAIL_USE_TLS = os.getenv('EMAIL_USE_TLS', 'true').lower() in ('1', 'true', 'yes')
EMAIL_USE_SSL = os.getenv('EMAIL_USE_SSL', 'false').lower() in ('1', 'true', 'yes')
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')
EMAIL_TIMEOUT = int(os.getenv('EMAIL_TIMEOUT', '15'))
EMAIL_BACKEND = os.getenv('EMAIL_BACKEND') or (
    'django.core.mail.backends.smtp.EmailBackend' if EMAIL_HOST_USER
    else 'django.core.mail.backends.console.EmailBackend'
)
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', EMAIL_HOST_USER or 'noreply@scheduling-api.local')

# Email 驗證碼註冊（apps.accounts.registration）：節流/有效期可用 env 微調
EMAIL_VERIFICATION = {
    'CODE_TTL_SECONDS': int(os.getenv('EMAIL_CODE_TTL_SECONDS', '600')),
    'RESEND_COOLDOWN_SECONDS': int(os.getenv('EMAIL_CODE_RESEND_COOLDOWN', '60')),
    'MAX_SENDS_PER_EMAIL_PER_HOUR': int(os.getenv('EMAIL_CODE_MAX_PER_EMAIL_HOUR', '5')),
    'MAX_SENDS_PER_IP_PER_HOUR': int(os.getenv('EMAIL_CODE_MAX_PER_IP_HOUR', '20')),
    'MAX_ATTEMPTS': int(os.getenv('EMAIL_CODE_MAX_ATTEMPTS', '5')),
}
# 只有本地開發（console backend）才在回應裡回 debug_code；生產絕不可開
EMAIL_VERIFICATION_EXPOSE_CODE = False

# AI Provider
AI_SCHEDULE_PROVIDER = os.getenv(
    'AI_SCHEDULE_PROVIDER',
    'apps.ai_engine.providers.ortools_provider.ORToolsProvider'
)

# Firebase
FIREBASE_CREDENTIALS_PATH = os.getenv('FIREBASE_CREDENTIALS_PATH', '')
