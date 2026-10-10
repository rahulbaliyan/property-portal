"""Phase 23: a minimal, staff-authenticated JSON API. Plain Django
JsonResponse views, not Django REST Framework — DRF would be a new
dependency this project doesn't otherwise carry, and every endpoint here
is either a straightforward read of already-built data or a single POST
trigger (pipeline.run_full_analysis), neither of which needs DRF's
serializer/viewset machinery to do well.

Session-authenticated (the same login as admin and the staff report
page), CSRF-protected like every other POST endpoint in this project —
not a separate token/API-key scheme, since every client of this API is,
for now, the same staff audience already using the rest of the app.
"""

from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_POST

from properties.models import Property

from . import pipeline
from .models import AnalysisRun


def _latest_run_for_property(property_obj):
    return AnalysisRun.objects.filter(property_listing=property_obj).order_by("-created_at").first()


def _decimal_str(value):
    return str(value) if value is not None else None


def _date_str(value):
    return value.isoformat() if value is not None else None


def _serialize_check(check):
    return {
        "category": check.category,
        "category_display": check.get_category_display(),
        "status": check.status,
        "status_display": check.get_status_display(),
        "notes": check.notes,
        "verification_required": check.verification_required,
    }


def _serialize_risk(risk):
    return {
        "category": risk.category,
        "category_display": risk.get_category_display(),
        "severity": risk.severity,
        "severity_display": risk.get_severity_display(),
        "reason": risk.reason,
        "recommended_verification": risk.recommended_verification,
    }


def _serialize_evidence(evidence):
    return {
        "claim": evidence.claim,
        "source_type": evidence.source_type,
        "verification_status": evidence.verification_status,
        "confidence": evidence.confidence,
    }


def _serialize_score(score):
    if score is None:
        return None
    return {
        "overall_score": score.overall_score,
        "confidence_score": score.confidence_score,
        "category_scores": score.category_scores,
        "missing_data_penalty_applied": score.missing_data_penalty_applied,
        "explanation": score.explanation,
        "computed_at": score.computed_at.isoformat() if score.computed_at else None,
    }


def _serialize_valuation(valuation_obj):
    if valuation_obj is None:
        return None
    return {
        "median_comparable_price_per_sqm": _decimal_str(valuation_obj.median_comparable_price_per_unit),
        "lower_range_price_per_sqm": _decimal_str(valuation_obj.lower_range_price_per_unit),
        "upper_range_price_per_sqm": _decimal_str(valuation_obj.upper_range_price_per_unit),
        "asking_price_per_sqm": _decimal_str(valuation_obj.asking_price_per_unit),
        "premium_discount_percent": _decimal_str(valuation_obj.premium_discount_percent),
        "comparable_count": valuation_obj.comparable_count,
        "methodology_notes": valuation_obj.methodology_notes,
    }


def _serialize_comparable(comparable):
    return {
        "title": comparable.title,
        "price": _decimal_str(comparable.price),
        "area_value": _decimal_str(comparable.area_value),
        "area_unit": comparable.area_unit,
        "price_per_unit": _decimal_str(comparable.price_per_unit),
        "is_verified_transaction": comparable.is_verified_transaction,
        "source": comparable.source,
        "transaction_date": _date_str(comparable.transaction_date),
    }


def _serialize_run(run):
    return {
        "id": run.pk,
        "status": run.status,
        "property_id": run.property_listing_id,
        "due_diligence_checks": [_serialize_check(c) for c in run.due_diligence_checks.all()],
        "risks": [_serialize_risk(r) for r in run.risks.all()],
        "evidence": [_serialize_evidence(e) for e in run.evidence_items.all()],
        "score": _serialize_score(getattr(run, "score", None)),
        "valuation": _serialize_valuation(getattr(run, "valuation", None)),
        "ai_summary": run.ai_summary or None,
        "created_at": run.created_at.isoformat(),
    }


@staff_member_required
@require_GET
def property_analysis(request, property_id):
    property_obj = get_object_or_404(Property, pk=property_id)
    run = _latest_run_for_property(property_obj)
    if run is None:
        return JsonResponse({"detail": "No analysis run exists for this property yet."}, status=404)
    return JsonResponse(_serialize_run(run))


@staff_member_required
@require_GET
def property_score(request, property_id):
    property_obj = get_object_or_404(Property, pk=property_id)
    run = _latest_run_for_property(property_obj)
    return JsonResponse({"score": _serialize_score(getattr(run, "score", None)) if run else None})


@staff_member_required
@require_GET
def property_risks(request, property_id):
    property_obj = get_object_or_404(Property, pk=property_id)
    run = _latest_run_for_property(property_obj)
    risks = run.risks.all() if run else []
    return JsonResponse({"risks": [_serialize_risk(r) for r in risks]})


@staff_member_required
@require_GET
def property_comparables(request, property_id):
    property_obj = get_object_or_404(Property, pk=property_id)
    return JsonResponse(
        {"comparables": [_serialize_comparable(c) for c in property_obj.comparables.all()]}
    )


@staff_member_required
@require_POST
def property_analyze(request, property_id):
    """Creates a run if this property has none yet, otherwise reuses the
    latest one — a repeat call re-analyzes in place rather than piling up
    runs, matching every individual step's own re-run-replaces behavior."""
    property_obj = get_object_or_404(Property, pk=property_id)
    run = _latest_run_for_property(property_obj)
    if run is None:
        run = AnalysisRun.objects.create(property_listing=property_obj, created_by=request.user)
    pipeline.run_full_analysis(run)
    return JsonResponse(_serialize_run(run))
