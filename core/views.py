from django.conf import settings
from django.contrib import messages
from django.db.models import Count
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.cache import cache_page
from django.views.decorators.http import require_GET, require_http_methods

from inquiries.forms import InquiryForm
from inquiries.notifications import notify_new_inquiry_async
from properties.models import Location, Property


def _available_properties():
    return (
        Property.objects.filter(moderation_status=Property.ModerationStatus.APPROVED)
        .exclude(status=Property.Status.SOLD)
    )


def _locations_with_live_counts():
    """Every region, annotated with a live (not hardcoded) count of
    currently-available listings — a region only shows a count once it
    actually has one."""
    counts_by_region = dict(
        _available_properties()
        .values_list("region")
        .annotate(count=Count("id"))
        .values_list("region", "count")
    )
    locations = list(Location.objects.all())
    for location in locations:
        location.listing_count = counts_by_region.get(location.region, 0)
    return locations


@cache_page(settings.PAGE_CACHE_SECONDS)
@require_GET
def home(request):
    available = _available_properties().prefetch_related("images")
    featured = list(available.filter(is_featured=True)[:6])
    recent = list(available.exclude(pk__in=[p.pk for p in featured])[:6])

    context = {
        "featured": featured,
        "recent": recent,
        "locations": _locations_with_live_counts(),
        "regions": Property.Region.choices,
        "property_types": Property.PropertyType.choices,
        "listing_intents": Property.ListingIntent.choices,
    }
    return render(request, "core/home.html", context)


@cache_page(settings.PAGE_CACHE_SECONDS)
@require_GET
def about(request):
    context = {
        "locations": _locations_with_live_counts(),
    }
    return render(request, "core/about.html", context)


@cache_page(settings.PAGE_CACHE_SECONDS)
@require_GET
def services(request):
    return render(request, "core/services.html")


@require_http_methods(["GET", "POST"])
def contact(request):
    if request.method == "POST":
        form = InquiryForm(request.POST)
        if form.is_valid():
            inquiry = form.save(commit=False)
            inquiry.property = None
            inquiry.save()
            notify_new_inquiry_async(inquiry)
            messages.success(request, "Thanks for reaching out — we'll get back to you soon.")
            return redirect("core:contact")
    else:
        form = InquiryForm()

    return render(request, "core/contact.html", {"form": form})


@require_GET
def robots_txt(request):
    # Always the canonical domain, matching every other canonical/OG URL
    # on the site — not request.get_host(), which would emit the
    # .onrender.com fallback host if crawled there instead.
    lines = [
        "User-agent: *",
        "Allow: /",
        f"Sitemap: https://{settings.PREFERRED_DOMAIN}/sitemap.xml",
    ]
    return HttpResponse("\n".join(lines), content_type="text/plain")


@require_GET
def google_site_verification(request):
    return HttpResponse(
        "google-site-verification: google6ad03570e70caaee.html",
        content_type="text/html",
    )
