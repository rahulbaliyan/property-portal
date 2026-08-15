import logging
import threading

import requests
from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)

CONTENT_TYPE_JSON = "application/json"


def notify_new_inquiry_async(inquiry):
    """Fire-and-forget: runs notifications in a background thread so the
    visitor's request completes immediately instead of waiting on
    email/WhatsApp round-trips (which can take several seconds each,
    longer if a provider is slow or unreachable)."""
    threading.Thread(target=_notify_new_inquiry, args=(inquiry,), daemon=True).start()


def notify_new_seller_submission_async(property_obj):
    """Same fire-and-forget pattern, for when a seller submits a
    listing via the public "Sell Your Property" form."""
    threading.Thread(
        target=_notify_new_seller_submission, args=(property_obj,), daemon=True
    ).start()


def _notify_new_inquiry(inquiry):
    """Best-effort notifications. Failures are logged, never raised —
    the inquiry is already saved, so a flaky email/WhatsApp provider
    must not turn into a 500 for the visitor."""
    property_title = inquiry.property.title if inquiry.property else "General inquiry"
    subject = f"New inquiry: {property_title}"
    body = (
        f"Name: {inquiry.name}\n"
        f"Phone: {inquiry.phone}\n"
        f"Email: {inquiry.email or '-'}\n"
        f"Property: {property_title}\n\n"
        f"Message:\n{inquiry.message or '-'}"
    )
    _dispatch_email(subject, body)
    _dispatch_whatsapp(inquiry.name, property_title, inquiry.phone)


def _notify_new_seller_submission(property_obj):
    admin_url = f"https://{settings.PREFERRED_DOMAIN}/admin/properties/property/{property_obj.pk}/change/"
    subject = f"New property submitted for review: {property_obj.title}"
    body = (
        "A seller submitted a new listing — it's hidden from the public "
        "site until you approve it in admin.\n\n"
        f"Title: {property_obj.title}\n"
        f"Type: {property_obj.get_property_type_display()}\n"
        f"Listing: {property_obj.get_listing_intent_display()}\n"
        f"Region: {property_obj.get_region_display()}\n"
        f"Area: {property_obj.area_value} {property_obj.get_area_unit_display()}\n"
        f"Price: {property_obj.price if property_obj.price else 'Not provided'}\n\n"
        f"Seller name: {property_obj.seller_name}\n"
        f"Seller phone: {property_obj.seller_phone}\n"
        f"Seller email: {property_obj.seller_email or '-'}\n\n"
        f"Description:\n{property_obj.description or '-'}\n\n"
        f"Review it here: {admin_url}"
    )
    _dispatch_email(subject, body)
    _dispatch_whatsapp(
        property_obj.seller_name, property_obj.title, property_obj.seller_phone
    )


def _dispatch_email(subject, body):
    if settings.BREVO_API_KEY:
        _send_email_via_brevo_api(subject, body)
    else:
        _send_email_via_smtp(subject, body)


def _send_email_via_smtp(subject, body):
    try:
        send_mail(
            subject,
            body,
            settings.DEFAULT_FROM_EMAIL,
            [settings.ADMIN_NOTIFICATION_EMAIL],
            fail_silently=False,
        )
    except Exception:
        logger.exception("Failed to send email notification")


def _send_email_via_brevo_api(subject, body):
    """Brevo's HTTPS API, used instead of SMTP in production — hosts
    like Render block outbound SMTP ports but never HTTPS."""
    payload = {
        "sender": {"name": settings.SITE_NAME, "email": settings.ADMIN_NOTIFICATION_EMAIL},
        "to": [{"email": settings.ADMIN_NOTIFICATION_EMAIL}],
        "subject": subject,
        "textContent": body,
    }
    headers = {
        "api-key": settings.BREVO_API_KEY,
        "Content-Type": CONTENT_TYPE_JSON,
        "Accept": CONTENT_TYPE_JSON,
    }
    try:
        response = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            json=payload,
            headers=headers,
            timeout=10,
        )
        response.raise_for_status()
    except requests.RequestException:
        logger.exception("Failed to send email notification via Brevo API")


def _dispatch_whatsapp(name, property_title, phone):
    if not (settings.WHATSAPP_CLOUD_API_TOKEN and settings.WHATSAPP_CLOUD_PHONE_NUMBER_ID):
        return

    recipients = [
        n
        for n in (settings.WHATSAPP_ADMIN_NUMBER, settings.WHATSAPP_ADMIN_NUMBER_2)
        if n
    ]
    for recipient in recipients:
        _send_whatsapp_to(recipient, name, property_title, phone)


def _send_whatsapp_to(recipient, name, property_title, phone):
    url = (
        f"https://graph.facebook.com/v21.0/"
        f"{settings.WHATSAPP_CLOUD_PHONE_NUMBER_ID}/messages"
    )
    payload = {
        "messaging_product": "whatsapp",
        "to": recipient,
        "type": "template",
        "template": {
            "name": settings.WHATSAPP_NOTIFY_TEMPLATE,
            "language": {"code": "en"},
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": name},
                        {"type": "text", "text": property_title},
                        {"type": "text", "text": phone},
                    ],
                }
            ],
        },
    }
    headers = {
        "Authorization": f"Bearer {settings.WHATSAPP_CLOUD_API_TOKEN}",
        "Content-Type": CONTENT_TYPE_JSON,
    }
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        response.raise_for_status()
    except requests.RequestException:
        logger.exception("Failed to send WhatsApp notification to %s", recipient)
