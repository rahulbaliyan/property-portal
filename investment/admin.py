"""Phase 1 admin registration — basic visibility/CRUD on every domain
model so they're inspectable from day one. Compute-heavy behavior (readonly
AI-populated fields, custom action buttons, audit-log wiring) lands with
the phases that actually populate those fields (due-diligence engine,
scoring engine, document extraction pipeline), mirroring how duediligence's
admin grew incrementally alongside its services module rather than upfront.
"""

from django.contrib import admin, messages
from django.http import HttpResponseForbidden, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse

from . import (
    ai_analyst,
    dashboard,
    data_quality,
    due_diligence,
    location_intelligence,
    pipeline,
    reports,
    risk_engine,
    scoring,
    services,
    valuation,
)
from .models import (
    AnalysisRun,
    DocumentAnalysis,
    DueDiligenceCheck,
    Evidence,
    FieldOverrideLog,
    PropertyComparable,
    PropertyDocument,
    PropertyRisk,
    PropertyScore,
    PropertyValuation,
    ScoringConfiguration,
    ScoringFactor,
    VerificationItem,
)


def _analysis_run_change_url(analysis_run):
    return reverse("admin:investment_analysisrun_change", args=[analysis_run.pk])


# Fields worth an audit trail when a human overrides an automated finding
# — Phase 19's explicit ask ("every manual override should be
# auditable"), previously unfulfilled: FieldOverrideLog has existed since
# Phase 1 but nothing ever wrote to it. Computed-only fields (scores,
# extracted data) are admin-readonly already and can't be hand-edited at
# all, so they need no entry here.
_AUDITED_FIELDS = {
    DueDiligenceCheck: ("status", "notes"),
    PropertyRisk: ("severity", "reason"),
    Evidence: ("verification_status",),
}


def _log_field_overrides(analysis_run, instance, user):
    """Compares instance's current DB state against its new in-memory
    state for each audited field, logging one FieldOverrideLog row per
    changed field. Must be called BEFORE the save actually happens — an
    existing pk with a not-yet-flushed change is what lets this read the
    "previous" value by re-querying."""
    if analysis_run is None or instance.pk is None:
        return  # a brand-new row isn't an override of anything
    tracked_fields = _AUDITED_FIELDS.get(type(instance))
    if not tracked_fields:
        return
    try:
        previous = type(instance).objects.get(pk=instance.pk)
    except type(instance).DoesNotExist:
        return
    for field_name in tracked_fields:
        old_value = getattr(previous, field_name)
        new_value = getattr(instance, field_name)
        if old_value != new_value:
            FieldOverrideLog.objects.create(
                analysis_run=analysis_run,
                field_name=f"{type(instance).__name__}.{field_name}",
                previous_value=str(old_value) if old_value else "",
                new_value=str(new_value) if new_value else "",
                changed_by=user,
            )


def _prepare_document_before_save(document, request):
    """Checksum must be computed from the file BEFORE it's handed to
    .save() — at this point document.file is still the raw upload (an
    InMemoryUploadedFile/TemporaryUploadedFile), trivially readable
    regardless of storage backend. Reading it back AFTER save() works
    fine against local FileSystemStorage (what every test uses) but
    crashed with a 500 in production: Cloudinary's raw-resource storage
    doesn't support re-opening a just-saved file for reading. Confirmed
    against a real production upload."""
    if not document.uploaded_by_id:
        document.uploaded_by = request.user
    if document.file and not document.checksum:
        document.checksum = services.compute_checksum(document.file)


def _duplicate_after_save(document):
    return services.find_duplicate_document(
        document.property_listing, document.checksum, exclude_pk=document.pk
    )


def _warn_if_duplicate(admin_instance, request, document, duplicate):
    if duplicate:
        admin_instance.message_user(
            request,
            f'Note: "{document}" has identical content to document #{duplicate.pk} '
            "already on this property — check whether this is an accidental duplicate.",
            level=messages.WARNING,
        )


class DueDiligenceCheckInline(admin.TabularInline):
    model = DueDiligenceCheck
    extra = 0


class PropertyRiskInline(admin.TabularInline):
    model = PropertyRisk
    extra = 0


class VerificationItemInline(admin.TabularInline):
    model = VerificationItem
    extra = 0


class PropertyDocumentInline(admin.TabularInline):
    model = PropertyDocument
    extra = 0
    fields = ("document_type", "file", "verification_status", "uploaded_at")
    readonly_fields = ("uploaded_at",)


class PropertyComparableInline(admin.TabularInline):
    model = PropertyComparable
    extra = 0
    # property_listing is filled in from the parent run in save_formset()
    # below (it's a required field, but a second FK the inline form has
    # no natural way to populate) — excluded here so the form doesn't ask
    # for it directly.
    exclude = ("property_listing",)


class PropertyScoreInline(admin.StackedInline):
    model = PropertyScore
    extra = 0
    # Written by scoring.compute_score() — never hand-entered.
    readonly_fields = (
        "configuration",
        "category_scores",
        "overall_score",
        "confidence_score",
        "missing_data_penalty_applied",
        "explanation",
        "computed_at",
    )


class PropertyValuationInline(admin.StackedInline):
    model = PropertyValuation
    extra = 0
    # Written by valuation.compute_valuation() — never hand-entered.
    readonly_fields = (
        "median_comparable_price_per_unit",
        "lower_range_price_per_unit",
        "upper_range_price_per_unit",
        "asking_price_per_unit",
        "premium_discount_percent",
        "comparable_count",
        "methodology_notes",
        "calculated_at",
    )


class FieldOverrideLogInline(admin.TabularInline):
    model = FieldOverrideLog
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(AnalysisRun)
class AnalysisRunAdmin(admin.ModelAdmin):
    list_display = ("id", "property_listing", "status", "created_by", "created_at", "completed_at")
    list_filter = ("status",)
    search_fields = ("property_listing__title",)
    autocomplete_fields = ["property_listing"]
    change_form_template = "admin/investment/analysisrun/change_form.html"
    change_list_template = "admin/investment/analysisrun/change_list.html"
    readonly_fields = ("ai_summary", "ai_summary_raw_response", "ai_summary_error", "ai_summary_generated_at")
    inlines = [
        DueDiligenceCheckInline,
        PropertyRiskInline,
        VerificationItemInline,
        PropertyDocumentInline,
        PropertyComparableInline,
        PropertyValuationInline,
        PropertyScoreInline,
        FieldOverrideLogInline,
    ]

    def save_formset(self, request, form, formset, change):
        """PropertyDocument and PropertyComparable both require
        property_listing (a separate FK from analysis_run), which the
        inline form never shows — added rows need it copied from the
        parent run's own property_listing, or they'd fail to save."""
        instances = formset.save(commit=False)
        for obj in formset.deleted_objects:
            obj.delete()
        for obj in instances:
            if hasattr(obj, "property_listing_id") and not obj.property_listing_id:
                obj.property_listing = form.instance.property_listing
            if formset.model is PropertyDocument:
                _prepare_document_before_save(obj, request)
            _log_field_overrides(form.instance, obj, request.user)
            obj.save()
        formset.save_m2m()

        if formset.model is PropertyDocument:
            for obj in instances:
                _warn_if_duplicate(self, request, obj, _duplicate_after_save(obj))

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "<int:object_id>/run-due-diligence/",
                self.admin_site.admin_view(self.run_due_diligence_view),
                name="investment_analysisrun_run_due_diligence",
            ),
            path(
                "<int:object_id>/compute-score/",
                self.admin_site.admin_view(self.compute_score_view),
                name="investment_analysisrun_compute_score",
            ),
            path(
                "<int:object_id>/compute-risks/",
                self.admin_site.admin_view(self.compute_risks_view),
                name="investment_analysisrun_compute_risks",
            ),
            path(
                "<int:object_id>/compute-valuation/",
                self.admin_site.admin_view(self.compute_valuation_view),
                name="investment_analysisrun_compute_valuation",
            ),
            path(
                "<int:object_id>/analyze-location/",
                self.admin_site.admin_view(self.analyze_location_view),
                name="investment_analysisrun_analyze_location",
            ),
            path(
                "<int:object_id>/check-data-quality/",
                self.admin_site.admin_view(self.check_data_quality_view),
                name="investment_analysisrun_check_data_quality",
            ),
            path(
                "<int:object_id>/run-full-analysis/",
                self.admin_site.admin_view(self.run_full_analysis_view),
                name="investment_analysisrun_run_full_analysis",
            ),
            path(
                "dashboard/",
                self.admin_site.admin_view(self.dashboard_view),
                name="investment_analysisrun_dashboard",
            ),
            path(
                "<int:object_id>/generate-summary/",
                self.admin_site.admin_view(self.generate_summary_view),
                name="investment_analysisrun_generate_summary",
            ),
            path(
                "<int:object_id>/report/",
                self.admin_site.admin_view(self.report_view),
                name="investment_analysisrun_report",
            ),
        ]
        return custom + urls

    def run_due_diligence_view(self, request, object_id):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        analysis_run = get_object_or_404(AnalysisRun, pk=object_id)
        if not self.has_change_permission(request, analysis_run):
            return HttpResponseForbidden()

        due_diligence.run_due_diligence(analysis_run)
        self.message_user(request, "Due diligence checks updated.")
        return redirect(_analysis_run_change_url(analysis_run))

    def compute_score_view(self, request, object_id):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        analysis_run = get_object_or_404(AnalysisRun, pk=object_id)
        if not self.has_change_permission(request, analysis_run):
            return HttpResponseForbidden()

        result = scoring.compute_score(analysis_run)
        self.message_user(
            request,
            f"Score: {result.overall_score}/100 (confidence {result.confidence_score}/100).",
        )
        return redirect(_analysis_run_change_url(analysis_run))

    def compute_valuation_view(self, request, object_id):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        analysis_run = get_object_or_404(AnalysisRun, pk=object_id)
        if not self.has_change_permission(request, analysis_run):
            return HttpResponseForbidden()

        result = valuation.compute_valuation(analysis_run)
        if result.comparable_count:
            self.message_user(
                request,
                f"Valuation computed from {result.comparable_count} comparable(s): "
                f"median ₹{result.median_comparable_price_per_unit}/sqm.",
            )
        else:
            self.message_user(request, "No usable comparables yet — add some below first.", level=messages.WARNING)
        return redirect(_analysis_run_change_url(analysis_run))

    def compute_risks_view(self, request, object_id):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        analysis_run = get_object_or_404(AnalysisRun, pk=object_id)
        if not self.has_change_permission(request, analysis_run):
            return HttpResponseForbidden()

        count = risk_engine.compute_risks(analysis_run)
        self.message_user(request, f"{count} risk(s) identified.")
        return redirect(_analysis_run_change_url(analysis_run))

    def analyze_location_view(self, request, object_id):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        analysis_run = get_object_or_404(AnalysisRun, pk=object_id)
        if not self.has_change_permission(request, analysis_run):
            return HttpResponseForbidden()

        location_intelligence.analyze_location(analysis_run)
        check = DueDiligenceCheck.objects.filter(
            analysis_run=analysis_run, category=DueDiligenceCheck.Category.LOCATION
        ).first()
        self.message_user(request, check.notes if check else "Location analysis complete.")
        return redirect(_analysis_run_change_url(analysis_run))

    def check_data_quality_view(self, request, object_id):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        analysis_run = get_object_or_404(AnalysisRun, pk=object_id)
        if not self.has_change_permission(request, analysis_run):
            return HttpResponseForbidden()

        count = data_quality.check_data_quality(analysis_run)
        self.message_user(request, f"{count} data-quality issue(s) found.")
        return redirect(_analysis_run_change_url(analysis_run))

    def run_full_analysis_view(self, request, object_id):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        analysis_run = get_object_or_404(AnalysisRun, pk=object_id)
        if not self.has_change_permission(request, analysis_run):
            return HttpResponseForbidden()

        pipeline.run_full_analysis(analysis_run)
        score = getattr(analysis_run, "score", None)
        self.message_user(
            request,
            f"Full analysis complete — score {score.overall_score}/100 "
            f"(confidence {score.confidence_score}/100)." if score else "Full analysis complete.",
        )
        return redirect(_analysis_run_change_url(analysis_run))

    def dashboard_view(self, request):
        context = {
            **self.admin_site.each_context(request),
            **dashboard.build_dashboard_context(),
            "title": "Investment Dashboard",
        }
        return render(request, "admin/investment/dashboard.html", context)

    def generate_summary_view(self, request, object_id):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        analysis_run = get_object_or_404(AnalysisRun, pk=object_id)
        if not self.has_change_permission(request, analysis_run):
            return HttpResponseForbidden()

        ai_analyst.generate_summary(analysis_run)
        analysis_run.refresh_from_db()
        self.message_user(request, "AI summary generated — review it before relying on it.")
        return redirect(_analysis_run_change_url(analysis_run))

    def report_view(self, request, object_id):
        analysis_run = get_object_or_404(AnalysisRun, pk=object_id)
        if not self.has_view_permission(request, analysis_run):
            return HttpResponseForbidden()
        return reports.render_report(request, analysis_run, self.admin_site)


class DocumentAnalysisInline(admin.StackedInline):
    model = DocumentAnalysis
    extra = 0
    # Every field here is written by services.analyze_document(), never
    # hand-entered — same readonly-computed-fields convention duediligence
    # uses for its own AI-extraction output.
    readonly_fields = (
        "extracted_text",
        "extracted_data",
        "raw_ai_response",
        "extraction_method",
        "confidence",
        "flagged_inconsistencies",
        "error_message",
        "analyzed_at",
    )


@admin.register(PropertyDocument)
class PropertyDocumentAdmin(admin.ModelAdmin):
    list_display = ("document_type", "property_listing", "verification_status", "uploaded_by", "uploaded_at")
    list_filter = ("document_type", "verification_status")
    search_fields = ("property_listing__title",)
    autocomplete_fields = ["property_listing"]
    inlines = [DocumentAnalysisInline]
    readonly_fields = ("checksum", "uploaded_by")
    change_form_template = "admin/investment/propertydocument/change_form.html"

    def save_model(self, request, obj, form, change):
        _prepare_document_before_save(obj, request)
        super().save_model(request, obj, form, change)
        _warn_if_duplicate(self, request, obj, _duplicate_after_save(obj))

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "<int:object_id>/analyze-ai/",
                self.admin_site.admin_view(self.analyze_document_view),
                name="investment_propertydocument_analyze_ai",
            ),
        ]
        # Custom pattern first — otherwise Django's own <path:object_id>/
        # catch-all could swallow it before it's reached.
        return custom + urls

    def analyze_document_view(self, request, object_id):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        document = get_object_or_404(PropertyDocument, pk=object_id)
        if not self.has_change_permission(request, document):
            return HttpResponseForbidden()

        services.analyze_document(document)
        document.refresh_from_db()
        self.message_user(request, document.get_verification_status_display())
        return redirect(reverse("admin:investment_propertydocument_change", args=[document.pk]))


@admin.register(Evidence)
class EvidenceAdmin(admin.ModelAdmin):
    list_display = ("claim_preview", "source_type", "property_listing", "verification_status", "created_at")
    list_filter = ("source_type", "verification_status")
    search_fields = ("claim", "property_listing__title")
    autocomplete_fields = ["property_listing", "analysis_run", "source_document"]

    @admin.display(description="Claim")
    def claim_preview(self, obj):
        return obj.claim[:80]

    def save_model(self, request, obj, form, change):
        _log_field_overrides(obj.analysis_run, obj, request.user)
        super().save_model(request, obj, form, change)


class ScoringFactorInline(admin.TabularInline):
    model = ScoringFactor
    extra = 1


@admin.register(ScoringConfiguration)
class ScoringConfigurationAdmin(admin.ModelAdmin):
    list_display = ("name", "property_type", "is_active", "updated_at")
    list_filter = ("is_active", "property_type")
    inlines = [ScoringFactorInline]


admin.site.register(PropertyRisk)
admin.site.register(DueDiligenceCheck)
admin.site.register(PropertyComparable)
admin.site.register(VerificationItem)
