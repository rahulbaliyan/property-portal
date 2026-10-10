"""Phase 1 admin registration — basic visibility/CRUD on every domain
model so they're inspectable from day one. Compute-heavy behavior (readonly
AI-populated fields, custom action buttons, audit-log wiring) lands with
the phases that actually populate those fields (due-diligence engine,
scoring engine, document extraction pipeline), mirroring how duediligence's
admin grew incrementally alongside its services module rather than upfront.
"""

from django.contrib import admin, messages
from django.http import HttpResponseForbidden, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect
from django.urls import path, reverse

from . import ai_analyst, due_diligence, reports, risk_engine, scoring, services, valuation
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
