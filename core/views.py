from django.conf import settings
from django.db.models import Count
from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.cache import cache_page
from django.views.decorators.http import require_GET

from properties.models import Location, Property


@cache_page(settings.PAGE_CACHE_SECONDS)
@require_GET
def home(request):
    available = (
        Property.objects.filter(moderation_status=Property.ModerationStatus.APPROVED)
        .exclude(status=Property.Status.SOLD)
        .prefetch_related("images")
    )
    featured = list(available.filter(is_featured=True)[:6])
    recent = list(available.exclude(pk__in=[p.pk for p in featured])[:6])

    # Live per-region counts (not hardcoded) for the locations section —
    # a region only shows a count once it actually has listings.
    counts_by_region = dict(
        available.values_list("region").annotate(count=Count("id")).values_list("region", "count")
    )
    locations = list(Location.objects.all())
    for location in locations:
        location.listing_count = counts_by_region.get(location.region, 0)

    context = {
        "featured": featured,
        "recent": recent,
        "locations": locations,
        "regions": Property.Region.choices,
        "property_types": Property.PropertyType.choices,
        "listing_intents": Property.ListingIntent.choices,
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
