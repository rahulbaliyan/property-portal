"""Request-parsing and rendering helpers for the duediligence admin pages.

Permission checks live in admin.py (where get_urls()/admin_view() already
are) — these functions assume the caller has already verified access, and
focus on turning a request into a response.
"""

from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import render

from . import bhulekh
from .sources import load_non_bhulekh_sources

CACHE_TTL_SECONDS = 3600  # politeness cache for the live gov-portal lookups,
# distinct from cache_page's HTTP-response caching (that's reserved for
# public GET views elsewhere in this project) — this caches upstream API
# data inside application code instead.


def ajax_districts(request):
    cache_key = "duediligence:bhulekh:districts"
    districts = cache.get(cache_key)
    if districts is None:
        try:
            session = bhulekh.new_session()
            districts = bhulekh.fetch_districts(session)
        except bhulekh.BhulekhError as exc:
            return JsonResponse({"error": str(exc)}, status=502)
        cache.set(cache_key, districts, CACHE_TTL_SECONDS)
    return JsonResponse({"districts": districts})


def ajax_tehsils(request):
    district_code = request.GET.get("district_code", "")
    if not district_code:
        return JsonResponse({"error": "district_code is required"}, status=400)
    cache_key = f"duediligence:bhulekh:tehsils:{district_code}"
    tehsils = cache.get(cache_key)
    if tehsils is None:
        try:
            session = bhulekh.new_session()
            tehsils = bhulekh.fetch_tehsils(session, district_code)
        except bhulekh.BhulekhError as exc:
            return JsonResponse({"error": str(exc)}, status=502)
        cache.set(cache_key, tehsils, CACHE_TTL_SECONDS)
    return JsonResponse({"tehsils": tehsils})


def ajax_villages(request):
    district_code = request.GET.get("district_code", "")
    tehsil_code = request.GET.get("tehsil_code", "")
    if not district_code or not tehsil_code:
        return JsonResponse({"error": "district_code and tehsil_code are required"}, status=400)
    cache_key = f"duediligence:bhulekh:villages:{district_code}:{tehsil_code}"
    villages = cache.get(cache_key)
    if villages is None:
        try:
            session = bhulekh.new_session()
            villages = bhulekh.fetch_villages(session, district_code, tehsil_code)
        except bhulekh.BhulekhError as exc:
            return JsonResponse({"error": str(exc)}, status=502)
        cache.set(cache_key, villages, CACHE_TTL_SECONDS)
    # Full list returned untouched — collisions (e.g. डांडालखौण्ड vs
    # चक डांडालखौंड) must stay visible so a human disambiguates, never
    # resolved here.
    return JsonResponse({"villages": villages})


def render_report(request, report, admin_site):
    context = {
        **admin_site.each_context(request),
        "report": report,
        "khasra_entries": report.khasra_entries.all(),
        "litigation_entries": [
            m
            for lookup in report.khata_lookups.all()
            for m in lookup.mutation_entries.filter(is_litigation_flagged=True)
        ],
        "manual_sources": load_non_bhulekh_sources(),
        "title": f"Title Check Report #{report.pk}",
    }
    return render(request, "duediligence/report.html", context)
