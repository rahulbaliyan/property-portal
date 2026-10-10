"""Phase 14: a plain staff-authenticated page for a property's analysis
report — the same data as the Django-admin report (investment/reports.py
builds both from one shared context), just without admin chrome. Staff-
only, not public: this tool handles seller/buyer names and deed data,
same access level as everything else in this app.
"""

from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET

from . import reports
from .models import AnalysisRun


@staff_member_required
@require_GET
def analysis_run_report(request, pk):
    analysis_run = get_object_or_404(AnalysisRun, pk=pk)
    return reports.render_staff_report(request, analysis_run)
