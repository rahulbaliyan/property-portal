"""Domain models for the AI Property Due-Diligence & Investment Scoring
System. Deliberately a separate app from duediligence — that app is a
single-purpose Bhulekh title-check tool (see duediligence/models.py); this
is a broader, multi-category evidence/scoring system that reuses
properties.Property as its subject (FK, same nullable-optional pattern as
duediligence.TitleCheckReport) rather than duplicating property fields.

Every category check (DueDiligenceCheck), risk (PropertyRisk), and piece of
evidence (Evidence) belongs to a specific AnalysisRun rather than directly
to the Property, so re-running analysis later adds history instead of
overwriting it — the same reasoning duediligence has for keeping a
KhataLookup row per check rather than one mutable snapshot.
"""

from django.conf import settings
from django.db import models

from properties.models import CLOUDINARY_IMAGE_BACKEND, Property


def property_document_storage():
    """Mirrors duediligence.models.deed_pdf_storage() — Cloudinary needs
    its raw resource type for arbitrary files (not the image/video
    resource types), or falls back to local disk in dev."""
    if settings.STORAGES["default"]["BACKEND"] == CLOUDINARY_IMAGE_BACKEND:
        from cloudinary_storage.storage import RawMediaCloudinaryStorage

        return RawMediaCloudinaryStorage()
    from django.core.files.storage import FileSystemStorage

    return FileSystemStorage()


class AnalysisRun(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        COMPLETE = "complete", "Complete"
        FAILED = "failed", "Failed"

    # Named `property_listing`, not `property` — a FK literally named
    # `property` would shadow the `property()` builtin for any @property
    # method on this model (see properties.models.PropertyVideo's own note
    # on this exact issue).
    property_listing = models.ForeignKey(
        Property, on_delete=models.CASCADE, related_name="analysis_runs"
    )
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Analysis run #{self.pk} — {self.property_listing} ({self.get_status_display()})"


class PropertyDocument(models.Model):
    class DocumentType(models.TextChoices):
        SALE_DEED = "sale_deed", "Sale Deed"
        REGISTRY = "registry", "Registry"
        KHASRA = "khasra", "Khasra"
        KHATAUNI = "khatauni", "Khatauni"
        MUTATION = "mutation", "Mutation Document"
        LAND_USE = "land_use", "Land-Use Document"
        NOC = "noc", "NOC"
        RERA = "rera", "RERA Document"
        BUILDER_APPROVAL = "builder_approval", "Builder Approval"
        SITE_PLAN = "site_plan", "Site Plan"
        LAYOUT_PLAN = "layout_plan", "Layout Plan"
        PROPERTY_TAX = "property_tax", "Property Tax Document"
        ENCUMBRANCE = "encumbrance", "Encumbrance Document"
        IDENTITY_OWNERSHIP = "identity_ownership", "Identity / Ownership Document"
        OTHER = "other", "Other Supporting Document"

    class VerificationStatus(models.TextChoices):
        UPLOADED = "uploaded", "Uploaded"
        PROCESSING = "processing", "Processing"
        EXTRACTED = "extracted", "Extracted"
        ANALYZED = "analyzed", "Analyzed"
        VERIFIED = "verified", "Verified"
        REJECTED = "rejected", "Rejected"
        NEEDS_REVIEW = "needs_review", "Needs Review"

    property_listing = models.ForeignKey(Property, on_delete=models.CASCADE, related_name="documents")
    analysis_run = models.ForeignKey(
        AnalysisRun, on_delete=models.SET_NULL, null=True, blank=True, related_name="documents"
    )
    document_type = models.CharField(max_length=25, choices=DocumentType.choices)
    file = models.FileField(upload_to="investment/documents/%Y/%m/", storage=property_document_storage)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    verification_status = models.CharField(
        max_length=15, choices=VerificationStatus.choices, default=VerificationStatus.UPLOADED
    )
    # SHA-256 hex digest — computed by the upload service (Phase 3), not
    # here, since the file isn't guaranteed fully written to storage
    # inside Model.save() itself (same reasoning duediligence.extract_deed
    # reads deed_pdf via a separate .open() call rather than in save()).
    checksum = models.CharField(max_length=64, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self):
        return f"{self.get_document_type_display()} — {self.property_listing}"


class DocumentAnalysis(models.Model):
    """AI-extracted structured data for one PropertyDocument. Kept as its
    own table (not fields bolted onto PropertyDocument) so the extraction
    schema can evolve — and be re-run — without migrating the document
    table itself, mirroring duediligence's KhataLookup/MutationEntry split
    between "the fetch" and "what was parsed out of it"."""

    document = models.OneToOneField(PropertyDocument, on_delete=models.CASCADE, related_name="analysis")
    extracted_text = models.TextField(blank=True)
    extracted_data = models.JSONField(
        default=dict, blank=True, help_text="Structured fields, schema-validated before being stored here."
    )
    raw_ai_response = models.TextField(blank=True, help_text="Audit trail — the model's raw response JSON.")
    extraction_method = models.CharField(max_length=50, blank=True)
    confidence = models.PositiveSmallIntegerField(null=True, blank=True)
    flagged_inconsistencies = models.TextField(blank=True)
    error_message = models.TextField(blank=True)
    analyzed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name_plural = "document analyses"

    def __str__(self):
        return f"Analysis of {self.document}"


class Evidence(models.Model):
    """Every important finding elsewhere in this app should point back to
    one of these — the core anti-hallucination mechanism: a claim without
    an Evidence row is not something the AI analyst (Phase 11) or report
    (Phase 12) may state as settled."""

    class SourceType(models.TextChoices):
        FACT = "fact", "Fact — Source-Backed"
        AI_ANALYSIS = "ai_analysis", "AI Analysis — Interpretation"
        ESTIMATE = "estimate", "Estimate — Model/Calculation"
        UNVERIFIED = "unverified", "Unverified — Requires Human Verification"

    class VerificationStatus(models.TextChoices):
        UNVERIFIED = "unverified", "Unverified"
        VERIFIED = "verified", "Verified"
        DISPUTED = "disputed", "Disputed"

    property_listing = models.ForeignKey(Property, on_delete=models.CASCADE, related_name="evidence_items")
    analysis_run = models.ForeignKey(
        AnalysisRun, on_delete=models.CASCADE, null=True, blank=True, related_name="evidence_items"
    )
    claim = models.TextField(help_text='e.g. "Property area is 150 gaj".')
    source_type = models.CharField(max_length=15, choices=SourceType.choices)
    source_document = models.ForeignKey(
        PropertyDocument, on_delete=models.SET_NULL, null=True, blank=True, related_name="evidence_items"
    )
    page_number = models.PositiveIntegerField(null=True, blank=True)
    source_url = models.URLField(blank=True)
    extracted_value = models.TextField(blank=True)
    confidence = models.PositiveSmallIntegerField(null=True, blank=True)
    verification_status = models.CharField(
        max_length=12, choices=VerificationStatus.choices, default=VerificationStatus.UNVERIFIED
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "evidence"

    def __str__(self):
        return self.claim[:80]


class DueDiligenceCheck(models.Model):
    class Category(models.TextChoices):
        OWNERSHIP = "ownership", "Ownership"
        TITLE_DOCUMENTATION = "title_documentation", "Title / Documentation"
        LAND_CLASSIFICATION = "land_classification", "Land Classification"
        LAND_USE = "land_use", "Land-Use"
        REGISTRY = "registry", "Registry"
        ENCUMBRANCE = "encumbrance", "Encumbrance"
        LITIGATION = "litigation", "Litigation Indicators"
        NOC_APPROVAL = "noc_approval", "NOC / Approval"
        RERA_COMPLIANCE = "rera_compliance", "RERA / Project Compliance"
        ROAD_ACCESS = "road_access", "Road / Access"
        UTILITIES = "utilities", "Utilities"
        LOCATION = "location", "Location"
        DEVELOPER = "developer", "Developer / Builder"
        MARKET_PRICE = "market_price", "Market Price"
        LIQUIDITY = "liquidity", "Liquidity"
        PROPERTY_CONDITION = "property_condition", "Property Condition"
        ENVIRONMENTAL = "environmental", "Environmental / Geographical Risk"

    class Status(models.TextChoices):
        PASS = "pass", "Pass"
        FAIL = "fail", "Fail"
        WARNING = "warning", "Warning"
        # Deliberately distinct from PASS — an unchecked category must
        # never silently read as clean. See risk/scoring engines (Phases
        # 6-7): UNKNOWN must always be treated as a confidence penalty,
        # never as an implicit pass.
        UNKNOWN = "unknown", "Unknown"
        NOT_APPLICABLE = "not_applicable", "Not Applicable"
        PENDING_VERIFICATION = "pending_verification", "Pending Verification"

    analysis_run = models.ForeignKey(AnalysisRun, on_delete=models.CASCADE, related_name="due_diligence_checks")
    category = models.CharField(max_length=25, choices=Category.choices)
    status = models.CharField(max_length=25, choices=Status.choices, default=Status.UNKNOWN)
    notes = models.TextField(blank=True)
    verification_required = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["category"]
        constraints = [
            models.UniqueConstraint(
                fields=["analysis_run", "category"], name="one_check_per_category_per_run"
            )
        ]

    def __str__(self):
        return f"{self.get_category_display()} — {self.get_status_display()}"


class PropertyRisk(models.Model):
    class Category(models.TextChoices):
        LEGAL = "legal", "Legal"
        PRICE = "price", "Price"
        LOCATION = "location", "Location"
        DEVELOPER = "developer", "Developer"
        DOCUMENT = "document", "Document"
        LIQUIDITY = "liquidity", "Liquidity"
        INFRASTRUCTURE = "infrastructure", "Infrastructure"
        ENVIRONMENTAL = "environmental", "Environmental"
        MARKET = "market", "Market"
        DATA_QUALITY = "data_quality", "Data Quality"

    class Severity(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        CRITICAL = "critical", "Critical"
        UNKNOWN = "unknown", "Unknown"

    analysis_run = models.ForeignKey(AnalysisRun, on_delete=models.CASCADE, related_name="risks")
    category = models.CharField(max_length=20, choices=Category.choices)
    severity = models.CharField(max_length=10, choices=Severity.choices)
    reason = models.TextField()
    recommended_verification = models.TextField(blank=True)
    evidence = models.ForeignKey(
        Evidence, on_delete=models.SET_NULL, null=True, blank=True, related_name="risks"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_severity_display()} — {self.reason[:60]}"


class PropertyComparable(models.Model):
    property_listing = models.ForeignKey(Property, on_delete=models.CASCADE, related_name="comparables")
    analysis_run = models.ForeignKey(
        AnalysisRun, on_delete=models.SET_NULL, null=True, blank=True, related_name="comparables"
    )
    title = models.CharField(max_length=200, blank=True)
    area_value = models.DecimalField(max_digits=10, decimal_places=2)
    area_unit = models.CharField(max_length=10, choices=Property.AreaUnit.choices)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    price_per_unit = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    transaction_date = models.DateField(
        null=True, blank=True, help_text="Listing or transaction date, whichever is known."
    )
    is_verified_transaction = models.BooleanField(
        default=False,
        help_text="False = listing price (unverified). True = a confirmed actual transaction — "
        "never treat these two as equivalent when computing a valuation range.",
    )
    source = models.CharField(max_length=200, blank=True)
    source_url = models.URLField(blank=True)
    distance_km = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    similarity_notes = models.TextField(blank=True)
    confidence = models.PositiveSmallIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title or f"Comparable #{self.pk}"


class PropertyValuation(models.Model):
    analysis_run = models.OneToOneField(AnalysisRun, on_delete=models.CASCADE, related_name="valuation")
    median_comparable_price_per_unit = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    lower_range_price_per_unit = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    upper_range_price_per_unit = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    asking_price_per_unit = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    premium_discount_percent = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    comparable_count = models.PositiveSmallIntegerField(default=0)
    methodology_notes = models.TextField(blank=True)
    calculated_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Valuation for run #{self.analysis_run_id}"


class ScoringConfiguration(models.Model):
    """Admin-editable — see docs/SOURCE_RESEARCH.md-style precedent
    (configs/sources.yaml) for this project's existing preference for
    config over hard-coded constants. Multiple configurations can exist
    (e.g. one per property_type); ScoringEngine (Phase 7) picks the
    active one matching the property being scored."""

    name = models.CharField(max_length=100)
    property_type = models.CharField(
        max_length=10,
        choices=Property.PropertyType.choices,
        blank=True,
        help_text="Leave blank to apply to every property type.",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_active", "name"]

    def __str__(self):
        return self.name


class ScoringFactor(models.Model):
    class Category(models.TextChoices):
        LEGAL_DOCUMENTATION = "legal_documentation", "Legal / Documentation"
        PRICE_VALUATION = "price_valuation", "Price / Valuation"
        LOCATION = "location", "Location"
        INFRASTRUCTURE = "infrastructure", "Infrastructure"
        MARKET = "market", "Market"
        LIQUIDITY = "liquidity", "Liquidity"
        PROPERTY_QUALITY = "property_quality", "Property Quality"

    configuration = models.ForeignKey(ScoringConfiguration, on_delete=models.CASCADE, related_name="factors")
    category = models.CharField(max_length=25, choices=Category.choices)
    weight_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        help_text="This category's weight, 0-100. All factors in one configuration should sum to 100.",
    )
    risk_multiplier = models.DecimalField(max_digits=4, decimal_places=2, default=1)

    class Meta:
        ordering = ["category"]
        constraints = [
            models.UniqueConstraint(
                fields=["configuration", "category"], name="one_factor_per_category_per_configuration"
            )
        ]

    def __str__(self):
        return f"{self.configuration.name}: {self.get_category_display()} ({self.weight_percent}%)"


class PropertyScore(models.Model):
    analysis_run = models.OneToOneField(AnalysisRun, on_delete=models.CASCADE, related_name="score")
    configuration = models.ForeignKey(
        ScoringConfiguration, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    category_scores = models.JSONField(
        default=dict,
        blank=True,
        help_text='e.g. {"legal_documentation": 72, "price_valuation": 81, ...} — each 0-100.',
    )
    overall_score = models.PositiveSmallIntegerField(null=True, blank=True)
    confidence_score = models.PositiveSmallIntegerField(null=True, blank=True)
    missing_data_penalty_applied = models.BooleanField(default=False)
    explanation = models.TextField(
        blank=True, help_text="Human-readable explanation of why the score is what it is — never just the number."
    )
    computed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Score {self.overall_score}/100 (confidence {self.confidence_score}) — run #{self.analysis_run_id}"


class VerificationItem(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        COMPLETED = "completed", "Completed"
        NOT_APPLICABLE = "not_applicable", "Not Applicable"

    analysis_run = models.ForeignKey(AnalysisRun, on_delete=models.CASCADE, related_name="verification_items")
    description = models.CharField(max_length=255)
    category = models.CharField(max_length=25, choices=DueDiligenceCheck.Category.choices, blank=True)
    is_required = models.BooleanField(default=True)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.PENDING)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["status", "id"]

    def __str__(self):
        return self.description


class FieldOverrideLog(models.Model):
    """Audit trail for a manual admin correction of an AI-extracted or
    computed value — required by this system's own product principle of
    keeping a complete audit trail for every override. Written by the
    admin layer (Phase 13) whenever a reviewer changes a value; this
    model only holds the record."""

    analysis_run = models.ForeignKey(AnalysisRun, on_delete=models.CASCADE, related_name="override_logs")
    field_name = models.CharField(max_length=100)
    previous_value = models.TextField(blank=True)
    new_value = models.TextField(blank=True)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    reason = models.TextField(blank=True)
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-changed_at"]

    def __str__(self):
        return f"{self.field_name}: {self.previous_value!r} → {self.new_value!r}"
