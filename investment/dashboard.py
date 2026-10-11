"""Phase 24: admin dashboard metrics. Read-only aggregate queries over
everything the rest of this app has already computed — no new
computation of its own, just counts/averages/groupings for an at-a-
glance view instead of clicking through individual AnalysisRuns one by
one.
"""

from django.db.models import Avg, Count

from properties.models import Property

from .models import AnalysisRun, DueDiligenceCheck, PropertyDocument, PropertyRisk, PropertyScore


def build_dashboard_context() -> dict:
    scores = PropertyScore.objects.exclude(overall_score__isnull=True)
    score_ranges = [
        ("0–39 — High risk", scores.filter(overall_score__lt=40).count()),
        ("40–69 — Review needed", scores.filter(overall_score__gte=40, overall_score__lt=70).count()),
        ("70–100 — Good", scores.filter(overall_score__gte=70).count()),
    ]
    avg_confidence = scores.aggregate(avg=Avg("confidence_score"))["avg"]

    pending_verification_count = (
        DueDiligenceCheck.objects.filter(
            status__in=[DueDiligenceCheck.Status.PENDING_VERIFICATION, DueDiligenceCheck.Status.UNKNOWN]
        )
        .values("analysis_run")
        .distinct()
        .count()
    )
    high_risk_count = (
        PropertyRisk.objects.filter(severity__in=[PropertyRisk.Severity.HIGH, PropertyRisk.Severity.CRITICAL])
        .values("analysis_run")
        .distinct()
        .count()
    )

    property_type_labels = dict(Property.PropertyType.choices)
    region_labels = dict(Property.Region.choices)
    by_property_type = [
        (property_type_labels.get(row["property_listing__property_type"], row["property_listing__property_type"]), row["count"])
        for row in AnalysisRun.objects.values("property_listing__property_type").annotate(count=Count("id")).order_by("-count")
    ]
    by_region = [
        (region_labels.get(row["property_listing__region"], row["property_listing__region"]), row["count"])
        for row in AnalysisRun.objects.values("property_listing__region").annotate(count=Count("id")).order_by("-count")
    ]

    return {
        "total_runs": AnalysisRun.objects.count(),
        "total_properties_analyzed": AnalysisRun.objects.values("property_listing").distinct().count(),
        "total_documents": PropertyDocument.objects.count(),
        "score_ranges": score_ranges,
        "avg_confidence": round(avg_confidence, 1) if avg_confidence is not None else None,
        "pending_verification_count": pending_verification_count,
        "high_risk_count": high_risk_count,
        "analysis_failures": AnalysisRun.objects.filter(status=AnalysisRun.Status.FAILED).count(),
        "by_property_type": by_property_type,
        "by_region": by_region,
    }
