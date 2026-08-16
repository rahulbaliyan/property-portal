"""v1 risk-level rules for TitleCheckReport.

Deterministic and explainable — precedence order matters, first match wins.
This reflects the Bhulekh Revenue cross-check ONLY, never a full-title
verdict (the other five source categories aren't automated — see
docs/SOURCE_RESEARCH.md and the report template's manual-checklist section).
"""

from .models import KhasraEntry, MutationEntry, TitleCheckReport


def compute_risk_level(report: TitleCheckReport) -> str:
    entries = list(report.khasra_entries.all())

    # 1. Red — a confirmed mismatch on data we actually retrieved.
    for entry in entries:
        if entry.area_match is False or entry.seller_match is False or entry.buyer_match is False:
            return TitleCheckReport.RiskLevel.RED

    found_entries = [e for e in entries if e.lookup_status == KhasraEntry.LookupStatus.FOUND]
    resolved_entries = [e for e in entries if e.lookup_status != KhasraEntry.LookupStatus.PENDING]

    # 2. Gray — the run failed outright, or nothing was even attempted
    #    (no khasras configured, or every entry is still PENDING despite
    #    the report claiming to be complete). A khasra that Bhulekh itself
    #    reported as NOT_FOUND is real data, not a failure — that's Yellow
    #    (rule 4), not Gray.
    if report.status == TitleCheckReport.Status.FAILED or not resolved_entries:
        return TitleCheckReport.RiskLevel.GRAY

    # 3. Orange — a technical lookup error on part of the run makes the
    #    result incomplete and less trustworthy than a clean "not found",
    #    so it's kept distinct from Yellow.
    if any(e.lookup_status == KhasraEntry.LookupStatus.ERROR for e in entries):
        return TitleCheckReport.RiskLevel.ORANGE

    not_found = any(e.lookup_status == KhasraEntry.LookupStatus.NOT_FOUND for e in entries)
    litigation_flagged = MutationEntry.objects.filter(
        khata_lookup__report=report, is_litigation_flagged=True
    ).exists()
    nothing_confirmed = all(
        e.area_match is None and e.seller_match is None and e.buyer_match is None
        for e in found_entries
    )

    # 4. Yellow — Bhulekh has no record for a khasra (a real government-data
    #    signal, not a scraper failure), or a litigation-flagged entry was
    #    found, or nothing was positively confirmable.
    if not_found or litigation_flagged or nothing_confirmed:
        return TitleCheckReport.RiskLevel.YELLOW

    # 5. Green — clean match, nothing mismatched, something was confirmed.
    return TitleCheckReport.RiskLevel.GREEN
