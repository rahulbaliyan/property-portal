"""Server-side verification for Cloudflare Turnstile — the widget alone
proves nothing; the token it produces has to be checked against
Cloudflare's API, or a bot could just submit the form without ever
loading the widget.
"""

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
REQUEST_TIMEOUT = 10


def is_turnstile_configured() -> bool:
    return bool(settings.TURNSTILE_SITE_KEY and settings.TURNSTILE_SECRET_KEY)


def verify_turnstile(request) -> bool:
    """True if the submission should proceed. Skips (returns True)
    entirely when Turnstile isn't configured — same graceful-degradation
    pattern as WhatsApp/Brevo elsewhere in this project, so the forms
    keep working before real keys are added. Once configured, a missing
    or invalid token fails closed (returns False)."""
    if not is_turnstile_configured():
        return True

    token = request.POST.get("cf-turnstile-response", "")
    if not token:
        return False

    try:
        response = requests.post(
            VERIFY_URL,
            data={
                "secret": settings.TURNSTILE_SECRET_KEY,
                "response": token,
                "remoteip": request.META.get("REMOTE_ADDR", ""),
            },
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return bool(response.json().get("success"))
    except requests.RequestException:
        logger.exception("Turnstile verification request failed")
        # Fails closed: if Cloudflare itself is unreachable, reject
        # rather than silently let unverified submissions through.
        return False
