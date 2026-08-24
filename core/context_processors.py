from django.conf import settings

from properties.models import Property


def site_settings(request):
    return {
        "settings_whatsapp_number": settings.WHATSAPP_NUMBER,
        "settings_whatsapp_number_2": settings.WHATSAPP_NUMBER_2,
        "site_name": settings.SITE_NAME,
        "site_tagline": settings.SITE_TAGLINE,
        "contact_phone_1": settings.CONTACT_PHONE_1,
        "contact_phone_2": settings.CONTACT_PHONE_2,
        "canonical_base_url": f"https://{settings.PREFERRED_DOMAIN}",
        # Used by base.html's nav "Locations" dropdown — global so every
        # page has it, not just the views that already build it for
        # their own filters.
        "nav_regions": Property.Region.choices,
        # Public key only — safe to expose. Blank until configured, and
        # every template checks this before rendering the widget.
        "turnstile_site_key": settings.TURNSTILE_SITE_KEY,
    }
