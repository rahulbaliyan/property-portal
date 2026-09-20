"""Phase 1 admin registration — basic visibility/CRUD on every domain
model so they're inspectable from day one. Compute-heavy behavior (readonly
AI-populated fields, custom action buttons, audit-log wiring) lands with
the phases that actually populate those fields (due-diligence engine,
scoring engine, document extraction pipeline), mirroring how duediligence's
admin grew incrementally alongside its services module rather than upfront.
"""

from django.contrib import admin, messages

from . import services
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


def _finalize_document_upload(document, request):
    """Shared by PropertyDocumentAdmin.save_model and AnalysisRunAdmin's
    inline formset save — checksum and uploaded_by must be set the same
    way regardless of which admin screen the upload came through."""
    changed = False
    if not document.uploaded_by_id:
        document.uploaded_by = request.user
        changed = True
    if document.file and not document.checksum:
        document.checksum = services.compute_checksum(document.file)
        changed = True
    if changed:
        document.save()
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


class PropertyValuationInline(admin.StackedInline):
    model = PropertyValuation
    extra = 0


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
            obj.save()
        formset.save_m2m()

        if formset.model is PropertyDocument:
            for obj in instances:
                duplicate = _finalize_document_upload(obj, request)
                _warn_if_duplicate(self, request, obj, duplicate)


class DocumentAnalysisInline(admin.StackedInline):
    model = DocumentAnalysis
    extra = 0


@admin.register(PropertyDocument)
class PropertyDocumentAdmin(admin.ModelAdmin):
    list_display = ("document_type", "property_listing", "verification_status", "uploaded_by", "uploaded_at")
    list_filter = ("document_type", "verification_status")
    search_fields = ("property_listing__title",)
    autocomplete_fields = ["property_listing"]
    inlines = [DocumentAnalysisInline]

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        duplicate = _finalize_document_upload(obj, request)
        _warn_if_duplicate(self, request, obj, duplicate)


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
