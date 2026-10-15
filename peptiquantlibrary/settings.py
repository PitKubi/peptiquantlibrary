"""PeptiQuant Library settings. Everything host-specific comes from environment
variables so the same tree runs on a laptop, pk-main and the production server
unchanged; the production values live in /etc/peptiquantlibrary/env."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('PQL_SECRET_KEY', 'dev-only-insecure-key')
DEBUG = os.environ.get('PQL_DEBUG', '1') == '1'
ALLOWED_HOSTS = [h for h in os.environ.get(
    'PQL_ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',') if h]
CSRF_TRUSTED_ORIGINS = [o for o in os.environ.get(
    'PQL_CSRF_TRUSTED_ORIGINS', '').split(',') if o]

# Shared secret for the /api/admin/ endpoints (deploy, sync, health, logs).
PQL_ADMIN_TOKEN = os.environ.get('PQL_ADMIN_TOKEN', '')

# Read-only DSN to the SysQuan production Postgres, used ONLY by
# `manage.py sync_catalog --from-db`. The public request path never touches it.
SYSQUAN_DB_DSN = os.environ.get('SYSQUAN_DB_DSN', '')

# Where quote requests are mailed. Comma-separated; empty disables mail.
PQL_QUOTE_NOTIFY_EMAILS = [e for e in os.environ.get(
    'PQL_QUOTE_NOTIFY_EMAILS', '').split(',') if e]

PQL_LOG_DIR = os.environ.get('PQL_LOG_DIR', str(BASE_DIR / 'logs'))

INSTALLED_APPS = [
    'django.contrib.contenttypes',
    'django.contrib.auth',
    'django.contrib.staticfiles',
    'catalog',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'peptiquantlibrary.urls'
WSGI_APPLICATION = 'peptiquantlibrary.wsgi.application'

TEMPLATES = [{
    'BACKEND': 'django.template.backends.django.DjangoTemplates',
    'DIRS': [],
    'APP_DIRS': True,
    'OPTIONS': {'context_processors': [
        'django.template.context_processors.request',
    ]},
}]

# The catalog is a few thousand rows: SQLite keeps the whole instance a single
# directory that can be copied to another server as-is ("moved in an instant").
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': os.environ.get('PQL_DB_PATH', str(BASE_DIR / 'db.sqlite3')),
    }
}

EMAIL_BACKEND = os.environ.get(
    'EMAIL_BACKEND', 'django.core.mail.backends.console.EmailBackend')
EMAIL_HOST = os.environ.get('EMAIL_HOST', '')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', '587'))
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS', '1') == '1'
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'library@localhost')

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'static_root'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'America/Montreal'
USE_TZ = True
