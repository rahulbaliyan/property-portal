"""Phase 6: the due-diligence engine. Computes one DueDiligenceCheck row
per category for an AnalysisRun from whatever evidence already exists.

The centerpiece is the bridge promised when `investment` was scoped
alongside duediligence (see CLAUDE.md's "The duediligence app" section):
duediligence.TitleCheckReport is the one real government cross-check this
project has (Bhulekh land records, via the admin-creds relay — see
docs/BHULEKH_RELAY.md). That verification is never redone here — this
module only reads its result and translates it into investment's own
Evidence/DueDiligenceCheck shape, for the Registry, Ownership, and
Litigation categories specifically (the three a Bhulekh check actually
speaks to).

Every other category has no automated source behind it yet (no comparable-
property engine, no location intelligence, no NOC/RERA document-presence
rules beyond what Phase 3 already extracts) — those are left as UNKNOWN
with verification_required=True rather than guessed at. UNKNOWN must never
silently read as PASS (see DueDiligenceCheck.Status's own docstring) —
that discipline applies here above all, since this is the module that
decides it.
"""

from duediligence.models import KhasraEntry, MutationEntry, TitleCheckReport

from .models import AnalysisRun, DueDiligenceCheck, Evidence

_RISK_TO_STATUS = {
    TitleCheckReport.RiskLevel.GREEN: DueDiligenceCheck.Status.PASS,
    TitleCheckReport.RiskLevel.YELLOW: DueDiligenceCheck.Status.WARNING,
    TitleCheckReport.RiskLevel.ORANGE: DueDiligenceCheck.Status.WARNING,
    TitleCheckReport.RiskLevel.RED: DueDiligenceCheck.Status.FAIL,
    TitleCheckReport.RiskLevel.GRAY: DueDiligenceCheck.Status.UNKNOWN,
}

# The three categories this module actively computes — re-run every time
# so they stay in sync with duediligence's latest result (that system is
# the authoritative source for them). Every other category is left alone
# once set, so a human's own manual review of e.g. Location isn't wiped
# out by re-running this.
_BRIDGED_CATEGORIES = {
    DueDiligenceCheck.Category.REGISTRY,
    DueDiligenceCheck.Category.OWNERSHIP,
    DueDiligenceCheck.Category.LITIGATION,
}


def _set_check(analysis_run, category, status, notes, verification_required=False):
    DueDiligenceCheck.objects.update_or_create(
        analysis_run=analysis_run,
        category=category,
        defaults={"status": status, "notes": notes, "verification_required": verification_required},
    )


def _latest_title_check_report(property_listing):
    if property_listing is None:
        return None
    return TitleCheckReport.objects.filter(property=property_listing).order_by("-created_at").first()


def _sync_registry_and_ownership(analysis_run, report):
    if report is None:
        _set_check(
            analysis_run, DueDiligenceCheck.Category.REGISTRY, DueDiligenceCheck.Status.PENDING_VERIFICATION,
            "No Bhulekh title check has been run for this property yet — "
            "run one in duediligence to verify against government records.",
            verification_required=True,
        )
        _set_check(
            analysis_run, DueDiligenceCheck.Category.OWNERSHIP, DueDiligenceCheck.Status.PENDING_VERIFICATION,
            "No Bhulekh title check has been run for this property yet.",
            verification_required=True,
        )
        return

    if report.status != TitleCheckReport.Status.COMPLETE:
        note = f"Bhulekh title check #{report.pk} is {report.get_status_display()} — not yet complete."
        _set_check(analysis_run, DueDiligenceCheck.Category.REGISTRY, DueDiligenceCheck.Status.PENDING_VERIFICATION, note, verification_required=True)
        _set_check(analysis_run, DueDiligenceCheck.Category.OWNERSHIP, DueDiligenceCheck.Status.PENDING_VERIFICATION, note, verification_required=True)
        return

    _set_check(
        analysis_run, DueDiligenceCheck.Category.REGISTRY, _RISK_TO_STATUS[report.risk_level],
        f"From Bhulekh title check #{report.pk}: {report.get_risk_level_display()}.",
    )

    entries = list(report.khasra_entries.all())
    found = [e for e in entries if e.lookup_status == KhasraEntry.LookupStatus.FOUND]
    if not found:
        ownership_status, ownership_notes = DueDiligenceCheck.Status.UNKNOWN, "No khasra entries were found in Bhulekh for this report."
    elif any(e.seller_match is False or e.buyer_match is False for e in found):
        ownership_status, ownership_notes = DueDiligenceCheck.Status.FAIL, "Seller/buyer name on the deed does not match Bhulekh's mutation record."
    elif all(e.seller_match is None and e.buyer_match is None for e in found):
        ownership_status, ownership_notes = DueDiligenceCheck.Status.UNKNOWN, "Seller/buyer names were not comparable (nothing declared or found)."
    else:
        ownership_status, ownership_notes = DueDiligenceCheck.Status.PASS, "Seller/buyer names match Bhulekh's mutation record."
    _set_check(analysis_run, DueDiligenceCheck.Category.OWNERSHIP, ownership_status, ownership_notes)

    property_listing = analysis_run.property_listing
    Evidence.objects.filter(analysis_run=analysis_run, claim__startswith="Bhulekh match —").delete()
    for entry in found:
        for label, matched, score in (
            ("Seller name", entry.seller_match, entry.seller_match_score),
            ("Buyer name", entry.buyer_match, entry.buyer_match_score),
            ("Area", entry.area_match, None),
            ("Consideration amount", entry.amount_match, None),
            ("Deed date", entry.date_match, None),
        ):
            if matched is None:
                continue
            claim = (
                f"Bhulekh match — Khasra {entry.khasra_number} — {label} "
                f"{'matches' if matched else 'does NOT match'} Bhulekh record"
            )
            if score is not None:
                claim += f" ({score}% similar)"
            Evidence.objects.create(
                property_listing=property_listing,
                analysis_run=analysis_run,
                claim=claim,
                source_type=Evidence.SourceType.FACT,
                extracted_value="match" if matched else "mismatch",
                verification_status=(
                    Evidence.VerificationStatus.VERIFIED if matched else Evidence.VerificationStatus.DISPUTED
                ),
            )


def _sync_litigation(analysis_run, report):
    if report is None or report.status != TitleCheckReport.Status.COMPLETE:
        _set_check(
            analysis_run, DueDiligenceCheck.Category.LITIGATION, DueDiligenceCheck.Status.PENDING_VERIFICATION,
            "No completed Bhulekh title check to scan for litigation language yet.",
            verification_required=True,
        )
        return

    flagged = MutationEntry.objects.filter(khata_lookup__report=report, is_litigation_flagged=True)
    if flagged.exists():
        keywords = "; ".join(sorted({k for m in flagged for k in m.matched_litigation_keywords.split(", ") if k}))
        _set_check(
            analysis_run, DueDiligenceCheck.Category.LITIGATION, DueDiligenceCheck.Status.WARNING,
            f"Litigation-related language found in the Bhulekh mutation history ({keywords}) "
            "— surfaced for human review, not auto-classified as a problem.",
            verification_required=True,
        )
    else:
        _set_check(
            analysis_run, DueDiligenceCheck.Category.LITIGATION, DueDiligenceCheck.Status.PASS,
            "No litigation-related language found in the Bhulekh mutation history.",
        )


_REMAINING_CATEGORIES = [c for c in DueDiligenceCheck.Category.values if c not in _BRIDGED_CATEGORIES]


def run_due_diligence(analysis_run: AnalysisRun) -> None:
    """The entrypoint the admin's "Run Due Diligence" button calls. Never
    raises — a missing/incomplete input source becomes an UNKNOWN or
    PENDING_VERIFICATION category, never an exception."""
    report = _latest_title_check_report(analysis_run.property_listing)
    _sync_registry_and_ownership(analysis_run, report)
    _sync_litigation(analysis_run, report)

    for category in _REMAINING_CATEGORIES:
        if DueDiligenceCheck.objects.filter(analysis_run=analysis_run, category=category).exists():
            continue  # leave a category a human has already reviewed alone
        _set_check(
            analysis_run, category, DueDiligenceCheck.Status.UNKNOWN,
            "Not yet automated for this category — see the manual checklist.",
            verification_required=True,
        )
