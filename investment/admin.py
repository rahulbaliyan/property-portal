"""Phase 1 admin registration — basic visibility/CRUD on every domain
model so they're inspectable from day one. Compute-heavy behavior (readonly
AI-populated fields, custom action buttons, audit-log wiring) lands with
the phases that actually populate those fields (due-diligence engine,
scoring engine, document extraction pipeline), mirroring how duediligence's
admin grew incrementally alongside its services module rather than upfront.
"""

from django.contrib import admin

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
