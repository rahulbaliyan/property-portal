from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import cache_page
from django.views.decorators.http import require_GET, require_http_methods

from inquiries.forms import InquiryForm
from inquiries.notifications import (
    notify_new_inquiry_async,
    notify_new_seller_submission_async,
)

from .forms import SellerListingForm
from .models import Property, PropertyImage


def _parse_decimal(value):
    try:
        return Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return None


@cache_page(settings.PAGE_CACHE_SECONDS)
@require_GET
def property_list(request):
    qs = (
        Property.objects.filter(moderation_status=Property.ModerationStatus.APPROVED)
        .exclude(status=Property.Status.SOLD)
        .prefetch_related("images")
    )

    q = request.GET.get("q", "").strip()
    region = request.GET.get("region", "")
    property_type = request.GET.get("property_type", "")
    listing_intent = request.GET.get("listing_intent", "")
    bedrooms_raw = request.GET.get("bedrooms", "").strip()
    min_price_raw = request.GET.get("min_price", "").strip()
    max_price_raw = request.GET.get("max_price", "").strip()
    sort = request.GET.get("sort", "newest")

    if q:
        qs = qs.filter(
            Q(title__icontains=q)
            | Q(address__icontains=q)
            | Q(description__icontains=q)
        )
    if region in Property.Region.values:
        qs = qs.filter(region=region)
    if property_type in Property.PropertyType.values:
        qs = qs.filter(property_type=property_type)
    if listing_intent in Property.ListingIntent.values:
        qs = qs.filter(listing_intent=listing_intent)

    # "4" means exactly 4, "4+" means 4 or more — matches how bedroom
    # counts are conventionally searched (a 5BHK buyer still wants to
    # see it under a "4+" filter, not just literal 4BHK listings).
    if bedrooms_raw.rstrip("+").isdigit():
        bedrooms = int(bedrooms_raw.rstrip("+"))
        if bedrooms_raw.endswith("+"):
            qs = qs.filter(bedrooms__gte=bedrooms)
        else:
            qs = qs.filter(bedrooms=bedrooms)

    min_price = _parse_decimal(min_price_raw)
    if min_price is not None:
        qs = qs.filter(price__gte=min_price)
    max_price = _parse_decimal(max_price_raw)
    if max_price is not None:
        qs = qs.filter(price__lte=max_price)

    sort_map = {
        "price_asc": "price",
        "price_desc": "-price",
        "newest": "-created_at",
        "featured": "-is_featured",
    }
    order = sort_map.get(sort, "-created_at")
    qs = qs.order_by(order, "-created_at") if order != "-created_at" else qs.order_by(order)

    paginator = Paginator(qs, 12)
    page_obj = paginator.get_page(request.GET.get("page"))

    context = {
        "page_obj": page_obj,
        "regions": Property.Region.choices,
        "property_types": Property.PropertyType.choices,
        "listing_intents": Property.ListingIntent.choices,
        "bedroom_options": [1, 2, 3, "4+"],
        "filters": {
            "q": q,
            "region": region,
            "property_type": property_type,
            "listing_intent": listing_intent,
            "bedrooms": bedrooms_raw,
            "min_price": min_price_raw,
            "max_price": max_price_raw,
            "sort": sort,
        },
    }
    return render(request, "properties/list.html", context)


@require_http_methods(["GET", "POST"])
def property_detail(request, slug):
    property_obj = get_object_or_404(
        Property.objects.filter(
            moderation_status=Property.ModerationStatus.APPROVED
        ).prefetch_related("images"),
        slug=slug,
    )

    if request.method == "POST":
        form = InquiryForm(request.POST)
        if form.is_valid():
            inquiry = form.save(commit=False)
            inquiry.property = property_obj
            inquiry.save()
            notify_new_inquiry_async(inquiry)
            messages.success(
                request,
                "Thanks! Your inquiry has been sent — we'll get back to you soon.",
            )
            return redirect(property_obj.get_absolute_url())
    else:
        form = InquiryForm()

    related = (
        Property.objects.filter(
            region=property_obj.region,
            moderation_status=Property.ModerationStatus.APPROVED,
        )
        .exclude(pk=property_obj.pk)
        .exclude(status=Property.Status.SOLD)
        .prefetch_related("images")[:3]
    )

    context = {"property": property_obj, "form": form, "related": related}
    return render(request, "properties/detail.html", context)


MAX_SELLER_PHOTOS = 10


@require_http_methods(["GET", "POST"])
def sell_property(request):
    if request.method == "POST":
        form = SellerListingForm(request.POST)
        if form.is_valid():
            property_obj = form.save(commit=False)
            property_obj.listing_source = Property.ListingSource.SELLER
            property_obj.moderation_status = Property.ModerationStatus.PENDING
            property_obj.save()

            photos = request.FILES.getlist("photos")[:MAX_SELLER_PHOTOS]
            for order, photo in enumerate(photos):
                PropertyImage.objects.create(
                    property=property_obj, image=photo, order=order
                )

            notify_new_seller_submission_async(property_obj)

            messages.success(
                request,
                "Thanks! Your listing has been submitted for review. "
                "Our team will reach out once it's approved and live.",
            )
            return redirect("properties:sell")
    else:
        form = SellerListingForm()

    return render(request, "properties/sell.html", {"form": form})
