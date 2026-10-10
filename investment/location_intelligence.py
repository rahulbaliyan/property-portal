"""Phase 9: location intelligence. Nearby schools, hospitals, transit,
and commercial areas around a property, via Google Maps Platform's
Places API (Nearby Search) — the provider this project's own research
recommended for this specific geography (hill towns/semi-rural
Uttarakhand, where OpenStreetMap coverage was flagged as likely thin).

Deliberately provider-agnostic at the module boundary
(fetch_nearby_places takes plain lat/lng in, returns plain dicts out) so
a different provider could be swapped in later without touching the
calling code in analyze_location() — same layering principle as
duediligence.bhulekh.py's pure HTTP-client boundary.

Known gap: Places API has no "road"/"highway" place type — nearby roads/
highways (part of Phase 9's original scope) aren't covered here. Would
need a different API (Google's Roads API) or a manual admin note; not
attempted, rather than guessed at.
"""

import logging
import math

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# Places API (New) — confirmed live that the legacy Nearby Search REST
# endpoint (maps.googleapis.com/maps/api/place/nearbysearch/json) returns
# REQUEST_DENIED on a freshly-created API key/project ("legacy API...not
# enabled"); Google enables the New API by default now, not the legacy
# one, so this module targets that instead.
PLACES_NEARBY_SEARCH_URL = "https://places.googleapis.com/v1/places:searchNearby"
REQUEST_TIMEOUT_SECONDS = 10
DEFAULT_RADIUS_METERS = 3000

# Google Places "type" values this module queries, mapped to a plain
# English label for display/Evidence text — a short, curated list
# matching Phase 9's own named categories (schools, hospitals, railway
# stations, airports, commercial areas), not every type Places supports.
PLACE_TYPES = {
    "school": "School",
    "hospital": "Hospital",
    "train_station": "Railway Station",
    "airport": "Airport",
    "shopping_mall": "Commercial Area",
}


class LocationIntelligenceError(Exception):
    """Base class for anything that goes wrong fetching location data."""


class LocationIntelligenceConfigError(LocationIntelligenceError):
    """GOOGLE_MAPS_API_KEY is not configured."""


class LocationIntelligenceRequestError(LocationIntelligenceError):
    """The API call itself failed (network, auth, rate limit, quota, bad status)."""


def is_configured() -> bool:
    return bool(settings.GOOGLE_MAPS_API_KEY)


def _haversine_km(lat1, lng1, lat2, lng2) -> float:
    """Great-circle distance in km — the exact spherical-earth formula,
    not an estimate. Good enough for "how far is the nearest school" at
    this scale (error from earth's actual oblateness is metres, not km,
    over distances like these)."""
    r = 6371.0  # mean earth radius, km
    phi1, phi2 = math.radians(float(lat1)), math.radians(float(lat2))
    d_phi = math.radians(float(lat2) - float(lat1))
    d_lambda = math.radians(float(lng2) - float(lng1))
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def fetch_nearby_places(latitude, longitude, place_type: str, radius_m: int = DEFAULT_RADIUS_METERS) -> list[dict]:
    """One Places Nearby Search call for a single place_type. Returns a
    list of {"name", "address", "distance_km"} sorted nearest-first, or
    raises LocationIntelligenceError — never silently returns an empty
    list on failure, so a caller can tell "no results nearby" (a real,
    useful signal for a remote property) from "the call itself failed"."""
    if not is_configured():
        raise LocationIntelligenceConfigError(
            "GOOGLE_MAPS_API_KEY is not configured — set it in the environment to enable location intelligence."
        )

    try:
        response = requests.post(
            PLACES_NEARBY_SEARCH_URL,
            json={
                "includedTypes": [place_type],
                "maxResultCount": 10,
                "locationRestriction": {
                    "circle": {
                        "center": {"latitude": float(latitude), "longitude": float(longitude)},
                        "radius": float(radius_m),
                    }
                },
            },
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": settings.GOOGLE_MAPS_API_KEY,
                # Required by Places API (New) — an unlisted field is
                # simply omitted from the response, not an error, so this
                # must name everything fetch_nearby_places actually reads.
                "X-Goog-FieldMask": "places.displayName,places.location,places.formattedAddress",
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        detail = str(exc)
        error_response = getattr(exc, "response", None)
        if error_response is not None:
            try:
                detail = error_response.json().get("error", {}).get("message", detail)
            except ValueError:
                pass
        logger.warning("Places API request failed (type=%s): %s", place_type, detail)
        raise LocationIntelligenceRequestError(f"Places API request failed: {detail}") from exc

    data = response.json()
    places = []
    for result in data.get("places", []):
        location = result.get("location", {})
        if "latitude" not in location or "longitude" not in location:
            continue
        places.append(
            {
                "name": result.get("displayName", {}).get("text", ""),
                "address": result.get("formattedAddress", ""),
                "distance_km": round(
                    _haversine_km(latitude, longitude, location["latitude"], location["longitude"]), 2
                ),
            }
        )
    places.sort(key=lambda p: p["distance_km"])
    return places


def analyze_location(analysis_run) -> None:
    """The entrypoint the admin's "Analyze Location" button calls. Never
    raises — a missing API key, missing coordinates, or a failed request
    all become a PENDING_VERIFICATION/WARNING DueDiligenceCheck with an
    explanatory note, never an unhandled exception."""
    from .models import DueDiligenceCheck, Evidence

    property_listing = analysis_run.property_listing

    def set_check(status, notes, verification_required=False):
        DueDiligenceCheck.objects.update_or_create(
            analysis_run=analysis_run,
            category=DueDiligenceCheck.Category.LOCATION,
            defaults={"status": status, "notes": notes, "verification_required": verification_required},
        )

    if property_listing is None or property_listing.latitude is None or property_listing.longitude is None:
        set_check(
            DueDiligenceCheck.Status.PENDING_VERIFICATION,
            "Property has no latitude/longitude set — cannot look up nearby amenities.",
            verification_required=True,
        )
        return

    if not is_configured():
        set_check(
            DueDiligenceCheck.Status.PENDING_VERIFICATION,
            "GOOGLE_MAPS_API_KEY is not configured.",
            verification_required=True,
        )
        return

    Evidence.objects.filter(analysis_run=analysis_run, claim__startswith="Nearby ").delete()
    found_any = False
    errors = []
    for place_type, label in PLACE_TYPES.items():
        try:
            places = fetch_nearby_places(property_listing.latitude, property_listing.longitude, place_type)
        except LocationIntelligenceError as exc:
            errors.append(f"{label}: {exc}")
            continue
        if not places:
            continue
        nearest = places[0]
        found_any = True
        Evidence.objects.create(
            property_listing=property_listing,
            analysis_run=analysis_run,
            claim=f"Nearby {label}: {nearest['name']} ({nearest['distance_km']} km)",
            source_type=Evidence.SourceType.FACT,
            source_url="https://maps.google.com",
            extracted_value=f"{nearest['distance_km']} km",
            verification_status=Evidence.VerificationStatus.VERIFIED,
        )

    if errors and not found_any:
        set_check(DueDiligenceCheck.Status.PENDING_VERIFICATION, "; ".join(errors), verification_required=True)
    elif found_any:
        set_check(DueDiligenceCheck.Status.PASS, "Nearby amenities found via Google Maps — see Evidence.")
    else:
        set_check(
            DueDiligenceCheck.Status.WARNING,
            "No schools/hospitals/transit/commercial areas found within 3km.",
            verification_required=True,
        )
