import os
from datetime import timedelta as _timedelta
from pathlib import Path

# Load .env file if it exists (dev only — production uses real env vars)
BASE_DIR = Path(__file__).resolve().parent.parent
_env_path = BASE_DIR / '.env'
if _env_path.exists():
    with open(_env_path, encoding='utf-8') as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith('#') and '=' in _line:
                _k, _, _v = _line.partition('=')
                os.environ.setdefault(_k.strip(), _v.strip())

DEBUG = os.environ.get('DEBUG', 'False') == 'True'

_default_secret = 'django-insecure-dev-only-not-for-production'
SECRET_KEY = os.environ.get('SECRET_KEY', _default_secret)
if not DEBUG and (not SECRET_KEY or SECRET_KEY == _default_secret):
    raise RuntimeError(
        'SECRET_KEY environment variable must be set to a strong random value in production. '
        'Generate one with: python -c "import secrets; print(secrets.token_hex(50))"'
    )

ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', 'localhost').split(',')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Third-party
    'axes',                    # brute-force login protection
    'crispy_forms',
    'crispy_bootstrap5',
    'django_tables2',
    'django_filters',
    # Self-paced platform
    'selfpaced',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',   # serve static files in production
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'axes.middleware.AxesMiddleware',               # must be after AuthenticationMiddleware
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'lce_platform.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'lce_platform.wsgi.application'

_db_engine = os.environ.get('DB_ENGINE', 'django.db.backends.sqlite3')
if _db_engine == 'django.db.backends.sqlite3':
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }
elif _db_engine == 'django.db.backends.mysql':
    DATABASES = {
        'default': {
            'ENGINE':   'django.db.backends.mysql',
            'NAME':     os.environ.get('DB_NAME',     'learnsync'),
            'USER':     os.environ.get('DB_USER',     'learnsync'),
            'PASSWORD': os.environ.get('DB_PASSWORD', ''),
            'HOST':     os.environ.get('DB_HOST',     'localhost'),
            'PORT':     os.environ.get('DB_PORT',     '3306'),
            'CONN_MAX_AGE': 0 if DEBUG else 60,  # 0 in dev avoids connection pile-up from threaded server + polling
            'OPTIONS': {
                'charset': 'utf8mb4',
                # STRICT_TRANS_TABLES: required by Django — MySQL 5.7+ enables this by default
                # but cPanel shared hosts sometimes revert to a permissive sql_mode.
                'init_command': "SET sql_mode='STRICT_TRANS_TABLES'",
            },
        }
    }
else:
    # PostgreSQL or other engine — passed through as-is
    DATABASES = {
        'default': {
            'ENGINE':   _db_engine,
            'NAME':     os.environ.get('DB_NAME',     'learnsync'),
            'USER':     os.environ.get('DB_USER',     'learnsync'),
            'PASSWORD': os.environ.get('DB_PASSWORD', ''),
            'HOST':     os.environ.get('DB_HOST',     'localhost'),
            'PORT':     os.environ.get('DB_PORT',     '5432'),
            'CONN_MAX_AGE': 60,
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'          # populated by collectstatic
STATICFILES_DIRS = [BASE_DIR / 'static']

# Private media — ingestion CSV files are stored here (not served via URL).
# On the server this resolves to a directory alongside the app; it is NOT
# inside public_html so the files are not web-accessible.
MEDIA_ROOT = BASE_DIR.parent / 'private_media'
MEDIA_URL = ''   # intentionally empty — these files are never served via URL

# Allow large CSV uploads (activity files are currently ~80 MB and growing).
# Django writes files > FILE_UPLOAD_MAX_MEMORY_SIZE to a temp file automatically;
# DATA_UPLOAD_MAX_MEMORY_SIZE guards non-file POST data (form fields only).
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024        # 5 MB → large files go to disk
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024       # 10 MB ceiling for form fields

# Django 4.2+ requires STORAGES dict instead of the removed STATICFILES_STORAGE setting.
# WhiteNoise's CompressedManifestStaticFilesStorage:
#   • compresses files (.gz / .br) so the web server can serve pre-compressed assets
#   • appends content hashes to filenames for long-lived browser caching
STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage',
    },
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ── CSRF trusted origins (required when the app is behind a reverse proxy) ─
_csrf_origins = os.environ.get('CSRF_TRUSTED_ORIGINS', '')
if _csrf_origins:
    CSRF_TRUSTED_ORIGINS = [o.strip() for o in _csrf_origins.split(',')]

# ── Logging ────────────────────────────────────────────────────────────────
# Ensure the logs directory exists so RotatingFileHandler never fails on startup.
_log_dir = BASE_DIR / 'logs'
_log_dir.mkdir(exist_ok=True)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {process:d} {thread:d} {message}',
            'style': '{',
        },
        'simple': {
            'format': '{levelname} {asctime} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'simple',
        },
        'file': {
            # Rotates at 5 MB, keeps 3 backups → max ~20 MB on disk.
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': str(_log_dir / 'learnsync.log'),
            'maxBytes': 5 * 1024 * 1024,
            'backupCount': 3,
            'formatter': 'verbose',
            'encoding': 'utf-8',
        },
    },
    'root': {
        'handlers': ['console', 'file'],
        'level': 'WARNING',
    },
    'loggers': {
        'django': {
            'handlers': ['console', 'file'],
            'level': os.environ.get('DJANGO_LOG_LEVEL', 'INFO'),
            'propagate': False,
        },
        'selfpaced': {
            'handlers': ['console', 'file'],
            'level': 'DEBUG',
            'propagate': False,
        },
    },
}

# Crispy Forms
CRISPY_ALLOWED_TEMPLATE_PACKS = 'bootstrap5'
CRISPY_TEMPLATE_PACK = 'bootstrap5'

# Django Tables2
DJANGO_TABLES2_TEMPLATE = 'django_tables2/bootstrap5.html'

LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = '/sp/'

# ── django-axes: brute-force login protection ──────────────────────────────────
# Locks a username+IP combination after AXES_FAILURE_LIMIT consecutive failures.
# Attempts are stored in the database — no Redis or extra infrastructure needed.
AUTHENTICATION_BACKENDS = [
    # AxesStandaloneBackend checks lockout status on every authentication attempt.
    # It must come first so a locked account is rejected before the password is checked.
    'axes.backends.AxesStandaloneBackend',
    'django.contrib.auth.backends.ModelBackend',
]

AXES_FAILURE_LIMIT = 5                           # lock after 5 consecutive bad passwords
AXES_COOLOFF_TIME = _timedelta(hours=1)          # auto-unlock after 1 hour
AXES_LOCKOUT_PARAMETERS = [                      # lock on EITHER username OR IP match
    ['username'],
    ['ip_address'],
]
AXES_RESET_ON_SUCCESS = True                     # clear counter on successful login
AXES_LOCKOUT_URL = '/accounts/login/'            # redirect locked users to login page
AXES_VERBOSE = False                             # suppress per-attempt console noise

# ── Production security headers ────────────────────────────────────────────
# These are safe to enable only when running behind HTTPS.
# In dev (DEBUG=True) they are left off so runserver works over plain HTTP.
if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31_536_000        # 1 year
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_BROWSER_XSS_FILTER = True
    X_FRAME_OPTIONS = 'DENY'

# ── Local overrides ────────────────────────────────────────────────────────
# local_settings.py is gitignored — use it for machine-specific dev overrides.
try:
    from .local_settings import *  # noqa: F401, F403
except ImportError:
    pass
