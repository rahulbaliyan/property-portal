"""Phase 12: the property report — pulls together everything an
AnalysisRun has computed into one page. Mirrors duediligence/views.py's
render_report() shape and reuses duediligence.sources' manual-checklist
registry directly rather than duplicating configs/sources.yaml — the
non-automated government sources are the same ones regardless of which
app's report is asking about them.
"""

from duediligence.sources import load_non_bhulekh_sources


def render_report(request, analysis_run, admin_site):
    from django.shortcuts import render

    context = {
        **admin_site.each_context(request),
        "run": analysis_run,
        "property": analysis_run.property_listing,
        "checks": analysis_run.due_diligence_checks.all().order_by("category"),
        "risks": analysis_run.risks.all().order_by("-severity", "-created_at"),
        "evidence": analysis_run.evidence_items.all().order_by("-created_at"),
        "documents": analysis_run.documents.all(),
        "score": getattr(analysis_run, "score", None),
        "manual_sources": load_non_bhulekh_sources(),
        "title": f"Property Analysis Report — Run #{analysis_run.pk}",
    }
    return render(request, "investment/report.html", context)
