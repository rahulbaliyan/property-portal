import logging

import requests
from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


def notify_new_inquiry(inquiry):
    """Best-effort notifications. Failures are logged, never raised —
    the inquiry is already saved, so a flaky email/WhatsApp provider
    must not turn into a 500 for the visitor."""
    _send_email(inquiry)
    _send_whatsapp(inquiry)


def _property_title(inquiry):
    return inquiry.property.title if inquiry.property else "General inquiry"


def _send_email(inquiry):
    subject = f"New inquiry: {_property_title(inquiry)}"
    body = (
        f"Name: {inquiry.name}\n"
        f"Phone: {inquiry.phone}\n"
        f"Email: {inquiry.email or '-'}\n"
        f"Property: {_property_title(inquiry)}\n\n"
        f"Message:\n{inquiry.message or '-'}"
    )
    try:
        send_mail(
            subject,
            body,
            settings.DEFAULT_FROM_EMAIL,
            [settings.ADMIN_NOTIFICATION_EMAIL],
            fail_silently=False,
        )
    except Exception:
        logger.exception("Failed to send inquiry email notification")


def _send_whatsapp(inquiry):
    if not (
        settings.WHATSAPP_CLOUD_API_TOKEN
        and settings.WHATSAPP_CLOUD_PHONE_NUMBER_ID
        and settings.WHATSAPP_ADMIN_NUMBER
    ):
        return

    url = (
        f"https://graph.facebook.com/v21.0/"
        f"{settings.WHATSAPP_CLOUD_PHONE_NUMBER_ID}/messages"
    )
    payload = {
        "messaging_product": "whatsapp",
        "to": settings.WHATSAPP_ADMIN_NUMBER,
        "type": "template",
        "template": {
            "name": settings.WHATSAPP_NOTIFY_TEMPLATE,
            "language": {"code": "en"},
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": inquiry.name},
                        {"type": "text", "text": _property_title(inquiry)},
                        {"type": "text", "text": inquiry.phone},
                    ],
                }
            ],
        },
    }
    headers = {
        "Authorization": f"Bearer {settings.WHATSAPP_CLOUD_API_TOKEN}",
        "Content-Type": "application/json",
    }
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        response.raise_for_status()
    except requests.RequestException:
        logger.exception("Failed to send WhatsApp inquiry notification")
