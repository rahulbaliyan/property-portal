"""Phase 12: the property report — pulls together everything an
AnalysisRun has computed into one page. Reuses duediligence.sources'
manual-checklist registry directly rather than duplicating
configs/sources.yaml — the non-automated government sources are the same
ones regardless of which app's report is asking about them.

Phase 14: build_report_context() is shared by two renderers — the
Django-admin version (render_admin_report, admin chrome, admin_site
required) and the plain staff-page version (render_staff_report, no
admin chrome, gated by @staff_member_required instead) — so the two
pages can never drift out of sync on what data they show.
"""

from duediligence.sources import load_non_bhulekh_sources


def build_report_context(analysis_run):
    return {
        "run": analysis_run,
        "property": analysis_run.property_listing,
        "checks": analysis_run.due_diligence_checks.all().order_by("category"),
        "risks": analysis_run.risks.all().order_by("-severity", "-created_at"),
        "evidence": analysis_run.evidence_items.all().order_by("-created_at"),
        "documents": analysis_run.documents.all(),
        "score": getattr(analysis_run, "score", None),
        "manual_sources": load_non_bhulekh_sources(),
    }


def render_report(request, analysis_run, admin_site):
    """The Django-admin version — admin chrome (sidebar/breadcrumbs),
    reached from AnalysisRun's change form."""
    from django.shortcuts import render

    context = {
        **admin_site.each_context(request),
        **build_report_context(analysis_run),
        "title": f"Property Analysis Report — Run #{analysis_run.pk}",
    }
    return render(request, "investment/report.html", context)


def render_staff_report(request, analysis_run):
    """The plain staff-page version (Phase 14) — no admin chrome, reached
    via its own URL rather than through Django admin. Still staff-only
    (the view wrapping this applies @staff_member_required) — this tool
    handles seller/buyer names and deed data, so it was never made
    public, only given a cleaner page than raw admin."""
    from django.shortcuts import render

    return render(request, "investment/staff_report.html", build_report_context(analysis_run))
