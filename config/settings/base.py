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
    "django.contrib.sitemaps",
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
    # Defining STORAGES at all replaces Django's own default entirely —
    # it does not merge in the normal "default" (media) entry for you.
    # Without this, every ImageField/FileField save raises
    # InvalidStorageError: "Could not find config for 'default'".
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}
# Legacy mirror of STORAGES["staticfiles"]["BACKEND"] — Django itself
# only reads STORAGES, but django-cloudinary-storage's collectstatic
# override checks this old-style setting directly and crashes if it's
# missing.
STATICFILES_STORAGE = STORAGES["staticfiles"]["BACKEND"]

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_REDIRECT_URL = "/admin/"

# In-process cache — fine given the single gunicorn worker (WEB_CONCURRENCY=1)
# this runs under on Render's free tier. It resets on every deploy/restart
# and wouldn't be shared if ever scaled to multiple workers; if that
# changes, swap this for a real cache server (e.g. Redis).
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "property-portal",
    }
}
# How long cached pages (home, listings) stay stale before a new
# admin-added/edited property shows up. GET-only pages with no
# per-visitor content, so this is safe to share across everyone.
PAGE_CACHE_SECONDS = env.int("PAGE_CACHE_SECONDS", default=300)

SITE_NAME = env("SITE_NAME", default="Shiv Shakti Developers")
SITE_TAGLINE = env("SITE_TAGLINE", default="Building Trust. Creating Spaces.")

# Canonical/OG URLs always point here regardless of which host (custom
# domain vs the .onrender.com fallback) actually served the request —
# otherwise both URLs get indexed as separate pages, splitting SEO
# signals for identical content.
PREFERRED_DOMAIN = env("PREFERRED_DOMAIN", default="shivashaktidevelopers.com")

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
# Without this, a blocked/unreachable SMTP port hangs the socket
# indefinitely — long enough for gunicorn's worker timeout to kill the
# whole worker (a SystemExit our notification code can't catch) instead
# of failing fast with a normal, loggable exception.
EMAIL_TIMEOUT = env.int("EMAIL_TIMEOUT", default=10)
DEFAULT_FROM_EMAIL = env(
    "DEFAULT_FROM_EMAIL", default=f"{SITE_NAME} <rahulbaliyan01@gmail.com>"
)
ADMIN_NOTIFICATION_EMAIL = env("ADMIN_NOTIFICATION_EMAIL", default="rahulbaliyan01@gmail.com")

# Render (and most PaaS free tiers) block outbound SMTP ports entirely,
# so the SMTP backend above can never reach Brevo's relay in production.
# When set, inquiries/notifications.py sends via Brevo's HTTPS API
# instead — same account, a transport that isn't blocked. Get this from
# Brevo → Settings → SMTP & API → API Keys (a different key than the
# SMTP key used above).
BREVO_API_KEY = env("BREVO_API_KEY", default="")

# WhatsApp: Meta WhatsApp Cloud API. All blank by default — notifications
# are skipped silently until these are configured (see inquiries/notifications.py).
WHATSAPP_CLOUD_API_TOKEN = env("WHATSAPP_CLOUD_API_TOKEN", default="")
WHATSAPP_CLOUD_PHONE_NUMBER_ID = env("WHATSAPP_CLOUD_PHONE_NUMBER_ID", default="")
WHATSAPP_NOTIFY_TEMPLATE = env("WHATSAPP_NOTIFY_TEMPLATE", default="new_inquiry")
# Numbers that receive the WhatsApp inquiry alert, digits + country code
# (e.g. "918332943533"). Default to the two contact numbers; leave either
# blank explicitly to notify only one number.
WHATSAPP_ADMIN_NUMBER = env("WHATSAPP_ADMIN_NUMBER", default=WHATSAPP_NUMBER)
WHATSAPP_ADMIN_NUMBER_2 = env("WHATSAPP_ADMIN_NUMBER_2", default=WHATSAPP_NUMBER_2)
