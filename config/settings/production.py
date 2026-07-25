from .base import *  # noqa: F401,F403

DEBUG = False

ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])

DATABASES = {
    "default": env.db("DATABASE_URL"),
}
# Without this, Django opens a fresh TCP+TLS+auth connection to Postgres
# on every single request and tears it down at the end — expensive over
# a WAN link to a remote host like Neon. Keep connections alive and
# reused across requests instead.
DATABASES["default"]["CONN_MAX_AGE"] = 600
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True

# HTTPS / security hardening — the host (e.g. Render) terminates TLS and
# forwards this header, so trust it to correctly detect HTTPS requests.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])

# Django's default logging only prints to console when DEBUG=True and
# otherwise tries to email admins (unconfigured here, so it fails
# silently). Always print request errors to stdout/stderr instead, so
# tracebacks show up in the host's log viewer without turning on DEBUG.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {"class": "logging.StreamHandler"},
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO",
        },
    },
}
