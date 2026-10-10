"""Phase 10: the risk engine. Derives PropertyRisk rows from
DueDiligenceCheck findings — never invents a risk from nothing (per this
system's product principles), only translates an already-computed FAIL/
WARNING status into the broader 10-category risk taxonomy, plus one
aggregate DATA_QUALITY risk when categories remain unverified (a buyer
should see "14 categories unchecked" as a risk in its own right, not just
a quiet absence).

Re-running clears and regenerates only the risks THIS function created
(tagged via a stable reason prefix, same pattern services.analyze_document
uses for its Evidence rows) — a risk an admin added by hand has no such
prefix and is left alone.
"""

from .models import AnalysisRun, DueDiligenceCheck, PropertyRisk

_AUTO_REASON_PREFIX = "[auto] "

# Not every DueDiligenceCheck category maps cleanly onto the 10 risk
# categories — PROPERTY_CONDITION in particular has no good home in this
# taxonomy and is deliberately left out here (still covered by the
# aggregate data-quality risk below if it's unverified).
_CATEGORY_TO_RISK = {
    DueDiligenceCheck.Category.OWNERSHIP: PropertyRisk.Category.LEGAL,
    DueDiligenceCheck.Category.TITLE_DOCUMENTATION: PropertyRisk.Category.LEGAL,
    DueDiligenceCheck.Category.LAND_CLASSIFICATION: PropertyRisk.Category.LEGAL,
    DueDiligenceCheck.Category.LAND_USE: PropertyRisk.Category.LEGAL,
    DueDiligenceCheck.Category.REGISTRY: PropertyRisk.Category.LEGAL,
    DueDiligenceCheck.Category.ENCUMBRANCE: PropertyRisk.Category.LEGAL,
    DueDiligenceCheck.Category.LITIGATION: PropertyRisk.Category.LEGAL,
    DueDiligenceCheck.Category.NOC_APPROVAL: PropertyRisk.Category.LEGAL,
    DueDiligenceCheck.Category.RERA_COMPLIANCE: PropertyRisk.Category.LEGAL,
    DueDiligenceCheck.Category.ROAD_ACCESS: PropertyRisk.Category.INFRASTRUCTURE,
    DueDiligenceCheck.Category.UTILITIES: PropertyRisk.Category.INFRASTRUCTURE,
    DueDiligenceCheck.Category.LOCATION: PropertyRisk.Category.LOCATION,
    DueDiligenceCheck.Category.DEVELOPER: PropertyRisk.Category.DEVELOPER,
    DueDiligenceCheck.Category.MARKET_PRICE: PropertyRisk.Category.MARKET,
    DueDiligenceCheck.Category.LIQUIDITY: PropertyRisk.Category.LIQUIDITY,
    DueDiligenceCheck.Category.ENVIRONMENTAL: PropertyRisk.Category.ENVIRONMENTAL,
}

_SEVERITY_BY_STATUS = {
    DueDiligenceCheck.Status.FAIL: PropertyRisk.Severity.HIGH,
    DueDiligenceCheck.Status.WARNING: PropertyRisk.Severity.MEDIUM,
}


def compute_risks(analysis_run: AnalysisRun) -> int:
    """The entrypoint the admin's "Identify Risks" button calls. Returns
    the number of risks now on this run (auto-generated + manual). Never
    raises — an empty/incomplete due-diligence checklist just means no
    risks are found yet, not an error."""
    PropertyRisk.objects.filter(analysis_run=analysis_run, reason__startswith=_AUTO_REASON_PREFIX).delete()

    checks = DueDiligenceCheck.objects.filter(analysis_run=analysis_run, status__in=_SEVERITY_BY_STATUS)
    for check in checks:
        risk_category = _CATEGORY_TO_RISK.get(check.category)
        if risk_category is None:
            continue
        PropertyRisk.objects.create(
            analysis_run=analysis_run,
            category=risk_category,
            severity=_SEVERITY_BY_STATUS[check.status],
            reason=f"{_AUTO_REASON_PREFIX}{check.get_category_display()}: {check.notes}",
            recommended_verification=(
                "Resolve before proceeding."
                if check.status == DueDiligenceCheck.Status.FAIL
                else "Review before proceeding."
            ),
        )

    unresolved = DueDiligenceCheck.objects.filter(
        analysis_run=analysis_run,
        status__in=[DueDiligenceCheck.Status.UNKNOWN, DueDiligenceCheck.Status.PENDING_VERIFICATION],
    ).count()
    if unresolved:
        PropertyRisk.objects.create(
            analysis_run=analysis_run,
            category=PropertyRisk.Category.DATA_QUALITY,
            severity=PropertyRisk.Severity.MEDIUM if unresolved > 5 else PropertyRisk.Severity.LOW,
            reason=f"{_AUTO_REASON_PREFIX}{unresolved} due-diligence categories are not yet verified.",
            recommended_verification="Complete the manual checklist for these categories.",
        )

    return PropertyRisk.objects.filter(analysis_run=analysis_run).count()
