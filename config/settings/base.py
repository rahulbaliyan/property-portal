"""
Shared settings for all environments. Environment-specific settings
(dev.py, production.py) import * from this module and override as needed.
"""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
env_file = BASE_DIR / ".env"
if env_file.exists():
    environ.Env.read_env(env_file)

SECRET_KEY = env("DJANGO_SECRET_KEY", default="django-insecure-dev-only-change-me")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # local apps
    "core",
    "properties",
    "inquiries",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.site_settings",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_REDIRECT_URL = "/admin/"

SITE_NAME = env("SITE_NAME", default="Shiv Shakti Developers")
SITE_TAGLINE = env("SITE_TAGLINE", default="Building Trust. Creating Spaces.")

# Contact numbers shown in the footer / Get in Touch section, in
# "+91XXXXXXXXXX" display form.
CONTACT_PHONE_1 = env("CONTACT_PHONE_1", default="+91 83329 43533")
CONTACT_PHONE_2 = env("CONTACT_PHONE_2", default="+91 95283 83995")

# Digits-only phone numbers (with country code, no "+") used for WhatsApp
# click-to-chat links, e.g. "919876543210". Default to CONTACT_PHONE_1/2.
# Leave either blank explicitly to hide that WhatsApp option.
_default_whatsapp = "".join(ch for ch in CONTACT_PHONE_1 if ch.isdigit())
_default_whatsapp_2 = "".join(ch for ch in CONTACT_PHONE_2 if ch.isdigit())
WHATSAPP_NUMBER = env("WHATSAPP_NUMBER", default=_default_whatsapp)
WHATSAPP_NUMBER_2 = env("WHATSAPP_NUMBER_2", default=_default_whatsapp_2)

# --- Inquiry notifications -------------------------------------------------
# Email: defaults to printing to the console so local dev needs no real
# SMTP credentials. Production sets EMAIL_BACKEND to the SMTP backend with
# Brevo's relay via env vars.
EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", default="smtp-relay.brevo.com")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
DEFAULT_FROM_EMAIL = env(
    "DEFAULT_FROM_EMAIL", default=f"{SITE_NAME} <rahulbaliyan01@gmail.com>"
)
ADMIN_NOTIFICATION_EMAIL = env("ADMIN_NOTIFICATION_EMAIL", default="rahulbaliyan01@gmail.com")

# WhatsApp: Meta WhatsApp Cloud API. All blank by default — notifications
# are skipped silently until these are configured (see inquiries/notifications.py).
WHATSAPP_CLOUD_API_TOKEN = env("WHATSAPP_CLOUD_API_TOKEN", default="")
WHATSAPP_CLOUD_PHONE_NUMBER_ID = env("WHATSAPP_CLOUD_PHONE_NUMBER_ID", default="")
WHATSAPP_NOTIFY_TEMPLATE = env("WHATSAPP_NOTIFY_TEMPLATE", default="new_inquiry")
# Number that receives the WhatsApp inquiry alert, digits + country code
# (e.g. "918332943533"). Defaults to the primary WhatsApp contact number.
WHATSAPP_ADMIN_NUMBER = env("WHATSAPP_ADMIN_NUMBER", default=WHATSAPP_NUMBER)
