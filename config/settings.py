import os
from pathlib import Path

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-only-secret-key")
DEBUG = os.getenv("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = os.getenv("DJANGO_ALLOWED_HOSTS", "*").split(",")


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'pagos',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / "templates"],
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

WSGI_APPLICATION = 'config.wsgi.application'


DB_ENGINE = os.getenv("DB_ENGINE", "django.db.backends.sqlite3")
if DB_ENGINE == "django.db.backends.postgresql":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("DB_NAME", "pagos_compras"),
            "USER": os.getenv("DB_USER", "postgres"),
            "PASSWORD": os.getenv("DB_PASSWORD", ""),
            "HOST": os.getenv("DB_HOST", "localhost"),
            "PORT": os.getenv("DB_PORT", "5432"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }


# Password validation
# https://docs.djangoproject.com/en/6.0/ref/settings/#auth-password-validators

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
# https://docs.djangoproject.com/en/6.0/topics/i18n/

LANGUAGE_CODE = "es-mx"
TIME_ZONE = "America/Mexico_City"

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.0/howto/static-files/

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

_BANXICO_FILE = BASE_DIR.parent / ".secrets" / "banxico.env"
_banxico_token_file = ""
if _BANXICO_FILE.exists():
    for _line in _BANXICO_FILE.read_text().splitlines():
        if _line.strip().startswith("BANXICO_TOKEN="):
            _banxico_token_file = _line.split("=", 1)[1].strip()
            break

BANXICO_TOKEN = os.getenv("BANXICO_TOKEN") or _banxico_token_file or ""
BANXICO_SERIE_ID = os.getenv("BANXICO_SERIE_ID", "SF60653")
BANXICO_TC_OBJETIVO = os.getenv("BANXICO_TC_OBJETIVO", "publicacion_dof")

# Reglas globales CFDI
CFDI_RFC_RECEPTOR_GLOBAL = os.getenv("CFDI_RFC_RECEPTOR_GLOBAL", "UAM140522Q51").strip().upper()

# Email (Gmail/API adapter can replace this transport later)
EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "1") == "1"
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", EMAIL_HOST_USER or "no-reply@localhost")
SOLICITUD_FACTURA_TEST_TO = os.getenv("SOLICITUD_FACTURA_TEST_TO", "bgonzalez@unamsa.mx")

GMAIL_OAUTH_CLIENT_FILE = os.getenv("GMAIL_OAUTH_CLIENT_FILE", str(BASE_DIR.parent / ".secrets" / "gmail_oauth_client.json"))
GMAIL_OAUTH_TOKEN_FILE = os.getenv("GMAIL_OAUTH_TOKEN_FILE", str(BASE_DIR.parent / ".secrets" / "gmail_oauth_token.json"))
GMAIL_OAUTH_INBOX_TOKEN_FILE = os.getenv("GMAIL_OAUTH_INBOX_TOKEN_FILE", str(BASE_DIR.parent / ".secrets" / "gmail_oauth_inbox_token.json"))

# Validación de beneficiario (titular cuenta vs emisor XML)
BENEFICIARY_MATCH_YELLOW_THRESHOLD = float(os.getenv("BENEFICIARY_MATCH_YELLOW_THRESHOLD", "0.45"))
GMAIL_OAUTH_SENDER = os.getenv("GMAIL_OAUTH_SENDER", "bgonzalez@unamsa.mx")

LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/accounts/login/"
