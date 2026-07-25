from django.conf import settings


def site_settings(request):
    return {
        "settings_whatsapp_number": settings.WHATSAPP_NUMBER,
        "site_name": settings.SITE_NAME,
        "site_tagline": settings.SITE_TAGLINE,
        "contact_phone_1": settings.CONTACT_PHONE_1,
        "contact_phone_2": settings.CONTACT_PHONE_2,
    }
