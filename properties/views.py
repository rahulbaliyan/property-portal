from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from inquiries.forms import InquiryForm
from inquiries.notifications import notify_new_inquiry_async

from .models import Property


def _parse_decimal(value):
    try:
        return Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return None


@require_GET
def property_list(request):
    qs = Property.objects.exclude(status=Property.Status.SOLD).prefetch_related(
        "images"
    )

    q = request.GET.get("q", "").strip()
    region = request.GET.get("region", "")
    property_type = request.GET.get("property_type", "")
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
    }
    qs = qs.order_by(sort_map.get(sort, "-created_at"))

    paginator = Paginator(qs, 12)
    page_obj = paginator.get_page(request.GET.get("page"))

    context = {
        "page_obj": page_obj,
        "regions": Property.Region.choices,
        "property_types": Property.PropertyType.choices,
        "filters": {
            "q": q,
            "region": region,
            "property_type": property_type,
            "min_price": min_price_raw,
            "max_price": max_price_raw,
            "sort": sort,
        },
    }
    return render(request, "properties/list.html", context)


@require_http_methods(["GET", "POST"])
def property_detail(request, slug):
    property_obj = get_object_or_404(
        Property.objects.prefetch_related("images"), slug=slug
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
        Property.objects.filter(region=property_obj.region)
        .exclude(pk=property_obj.pk)
        .exclude(status=Property.Status.SOLD)
        .prefetch_related("images")[:3]
    )

    context = {"property": property_obj, "form": form, "related": related}
    return render(request, "properties/detail.html", context)
