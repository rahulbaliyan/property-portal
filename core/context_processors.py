from django.conf import settings


def site_settings(request):
    return {"settings_whatsapp_number": settings.WHATSAPP_NUMBER}
