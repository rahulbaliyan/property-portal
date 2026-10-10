"""Orchestrates the fast, deterministic part of the analysis pipeline in
one call — due diligence, risk identification, data-quality checks,
valuation, and scoring. Shared by the admin's "Run Full Analysis" button
and the API's POST .../analyze/ endpoint (Phase 23), so the two can never
drift out of sync on what "a full analysis" actually runs.

Deliberately excludes location_intelligence.analyze_location() and
ai_analyst.generate_summary() — both are slow, network-dependent, and
(for location) may not even be configured, unlike the five steps here,
which are all fast pure-DB operations. Those two stay separate, deliberate
steps, same reasoning this project already applies to Analyze with AI
vs. document upload — the fast and slow operations don't get bundled.
"""

from . import data_quality, due_diligence, risk_engine, scoring, valuation
from .models import AnalysisRun


def run_full_analysis(analysis_run: AnalysisRun) -> AnalysisRun:
    due_diligence.run_due_diligence(analysis_run)
    risk_engine.compute_risks(analysis_run)
    data_quality.check_data_quality(analysis_run)
    valuation.compute_valuation(analysis_run)
    scoring.compute_score(analysis_run)
    return analysis_run
