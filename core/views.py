from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.cache import cache_page
from django.views.decorators.http import require_GET

from properties.models import Property


@cache_page(settings.PAGE_CACHE_SECONDS)
@require_GET
def home(request):
    available = Property.objects.exclude(status=Property.Status.SOLD).prefetch_related(
        "images"
    )
    featured = list(available.filter(is_featured=True)[:6])
    recent = list(available.exclude(pk__in=[p.pk for p in featured])[:6])

    context = {
        "featured": featured,
        "recent": recent,
        "regions": Property.Region.choices,
        "property_types": Property.PropertyType.choices,
    }
    return render(request, "core/home.html", context)


@require_GET
def robots_txt(request):
    lines = [
        "User-agent: *",
        "Allow: /",
        f"Sitemap: {request.scheme}://{request.get_host()}/sitemap.xml",
    ]
    return HttpResponse("\n".join(lines), content_type="text/plain")


@require_GET
def google_site_verification(request):
    return HttpResponse(
        "google-site-verification: google6ad03570e70caaee.html",
        content_type="text/html",
    )
