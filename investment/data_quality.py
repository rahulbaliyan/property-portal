"""Phase 17: the data-quality engine. The checks the spec calls out by
name: missing fields, conflicting values across documents, invalid
coordinates, and stale comparable data. Surfaces findings as
PropertyRisk(category=DATA_QUALITY) rows — never decides which of two
conflicting values is correct, only that a conflict exists and needs a
human to resolve it (same restraint duediligence.services already
applies to its own area/seller/buyer/amount/date matching).

Separate auto-tag ([auto-dq]) from risk_engine.py's own DATA_QUALITY risk
([auto]) so re-running either doesn't clobber the other's findings.
"""

import re
from datetime import timedelta

from django.utils import timezone

from .models import AnalysisRun, Evidence, PropertyComparable, PropertyRisk

_AUTO_REASON_PREFIX = "[auto-dq] "

# A gross sanity check against fat-fingered data entry (e.g. swapped
# lat/lng, a stray 0,0) — not a precise border.
_INDIA_LAT_RANGE = (6.0, 37.5)
_INDIA_LNG_RANGE = (68.0, 97.5)

_STALE_COMPARABLE_DAYS = 548  # ~18 months

_CLAIM_TOPIC_RE = re.compile(r"^([^:]+):")


def _create_risk(analysis_run, severity, reason, recommended_verification=""):
    PropertyRisk.objects.create(
        analysis_run=analysis_run,
        category=PropertyRisk.Category.DATA_QUALITY,
        severity=severity,
        reason=f"{_AUTO_REASON_PREFIX}{reason}",
        recommended_verification=recommended_verification,
    )


def _check_missing_fields(analysis_run, property_listing):
    if property_listing is None:
        return
    missing = []
    if not property_listing.price:
        missing.append("asking price")
    if not property_listing.area_value:
        missing.append("area")
    if property_listing.latitude is None or property_listing.longitude is None:
        missing.append("coordinates")
    if missing:
        _create_risk(
            analysis_run,
            PropertyRisk.Severity.LOW,
            f"Property is missing: {', '.join(missing)}.",
            "Fill in these fields for a more complete analysis.",
        )


def _check_invalid_coordinates(analysis_run, property_listing):
    if property_listing is None or property_listing.latitude is None or property_listing.longitude is None:
        return
    lat, lng = float(property_listing.latitude), float(property_listing.longitude)
    if lat == 0 and lng == 0:
        _create_risk(
            analysis_run,
            PropertyRisk.Severity.MEDIUM,
            "Coordinates are exactly (0, 0) — almost certainly unset/placeholder data, not a real location.",
            "Re-enter the correct latitude/longitude.",
        )
    elif not (
        _INDIA_LAT_RANGE[0] <= lat <= _INDIA_LAT_RANGE[1] and _INDIA_LNG_RANGE[0] <= lng <= _INDIA_LNG_RANGE[1]
    ):
        _create_risk(
            analysis_run,
            PropertyRisk.Severity.MEDIUM,
            f"Coordinates ({lat}, {lng}) fall outside India's bounding box — likely a data-entry error.",
            "Re-enter the correct latitude/longitude.",
        )


def _check_conflicting_evidence(analysis_run):
    """Groups FACT Evidence by its claim's label (e.g. "Property Area")
    and flags when two DIFFERENT source documents report a materially
    different value for the same thing. Never decides which is right."""
    evidence = Evidence.objects.filter(
        analysis_run=analysis_run, source_type=Evidence.SourceType.FACT, source_document__isnull=False
    ).select_related("source_document")

    by_topic: dict[str, list[Evidence]] = {}
    for item in evidence:
        match = _CLAIM_TOPIC_RE.match(item.claim)
        if not match:
            continue
        by_topic.setdefault(match.group(1).strip(), []).append(item)

    for topic, items in by_topic.items():
        values_to_items: dict[str, list[Evidence]] = {}
        for item in items:
            values_to_items.setdefault(item.extracted_value, []).append(item)
        if len(values_to_items) <= 1:
            continue
        documents = {item.source_document_id for group in values_to_items.values() for item in group}
        if len(documents) <= 1:
            continue  # same document, not a cross-document conflict
        summary = "; ".join(f'"{value}"' for value in values_to_items if value)
        _create_risk(
            analysis_run,
            PropertyRisk.Severity.MEDIUM,
            f"{topic} reported differently across documents: {summary}.",
            "Compare the source documents and confirm which value is correct.",
        )


def _check_stale_comparables(analysis_run):
    stale_cutoff = timezone.now().date() - timedelta(days=_STALE_COMPARABLE_DAYS)
    stale_count = PropertyComparable.objects.filter(
        analysis_run=analysis_run, transaction_date__lt=stale_cutoff
    ).count()
    if stale_count:
        _create_risk(
            analysis_run,
            PropertyRisk.Severity.LOW,
            f"{stale_count} comparable(s) are over 18 months old — market conditions may have changed.",
            "Look for more recent comparable transactions if available.",
        )


def check_data_quality(analysis_run: AnalysisRun) -> int:
    """The entrypoint the admin's "Check Data Quality" button calls.
    Returns the number of data-quality risks now on this run (auto +
    whatever risk_engine.py's own aggregate already added). Never
    raises — every check here is defensive against missing data by
    design, not by exception-handling."""
    PropertyRisk.objects.filter(
        analysis_run=analysis_run,
        category=PropertyRisk.Category.DATA_QUALITY,
        reason__startswith=_AUTO_REASON_PREFIX,
    ).delete()

    _check_missing_fields(analysis_run, analysis_run.property_listing)
    _check_invalid_coordinates(analysis_run, analysis_run.property_listing)
    _check_conflicting_evidence(analysis_run)
    _check_stale_comparables(analysis_run)

    return PropertyRisk.objects.filter(analysis_run=analysis_run, category=PropertyRisk.Category.DATA_QUALITY).count()
