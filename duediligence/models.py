from django.conf import settings
from django.db import models

from properties.models import CLOUDINARY_IMAGE_BACKEND, Property


def deed_pdf_storage():
    """Mirrors properties/models.py's video_storage() — Cloudinary needs
    its raw (non-image, non-video) resource type for arbitrary files like
    a scanned deed PDF, or falls back to local disk in dev."""
    if settings.STORAGES["default"]["BACKEND"] == CLOUDINARY_IMAGE_BACKEND:
        from cloudinary_storage.storage import RawMediaCloudinaryStorage

        return RawMediaCloudinaryStorage()
    from django.core.files.storage import FileSystemStorage

    return FileSystemStorage()


class TitleCheckReport(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft — Not Yet Checked"
        COMPLETE = "complete", "Check Complete"
        FAILED = "failed", "Check Failed"

    class RiskLevel(models.TextChoices):
        GREEN = "green", "Green — Clean Match"
        YELLOW = "yellow", "Yellow — Review Needed"
        ORANGE = "orange", "Orange — Partial/Unreliable Result"
        RED = "red", "Red — Mismatch Found"
        GRAY = "gray", "Gray — Lookup Failed"

    class DeedAreaUnit(models.TextChoices):
        SQM = "sqm", "Sq. Metres (matches Bhulekh)"
        SQFT = "sqft", "Sq. Ft."
        NALI = "nali", "Nali"
        BIGHA = "bigha", "Bigha"

    property = models.ForeignKey(
        Property,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="title_check_reports",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    # Location, filled in via the live cascading District/Tehsil/Village
    # picker (see bhulekh.py) — never free text, to avoid the
    # similarly-named-village trap (e.g. डांडालखौण्ड vs चक डांडालखौंड).
    district_name = models.CharField(max_length=100, blank=True)
    district_code = models.CharField(max_length=20, blank=True)
    tehsil_name = models.CharField(max_length=100, blank=True)
    tehsil_code = models.CharField(max_length=20, blank=True)
    village_name = models.CharField(max_length=150, blank=True)
    village_code = models.CharField(max_length=20, blank=True)
    pargana_name = models.CharField(max_length=100, blank=True)
    pargana_code = models.CharField(max_length=20, blank=True)

    # Deed metadata, typed in by the admin after reading the deed.
    deed_date = models.DateField(null=True, blank=True)
    consideration_amount = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    seller_name = models.CharField(max_length=200, blank=True)
    buyer_name = models.CharField(max_length=200, blank=True)
    registration_number = models.CharField(max_length=50, blank=True)
    book_number = models.CharField(max_length=50, blank=True)
    volume_number = models.CharField(max_length=50, blank=True)
    page_number = models.CharField(max_length=50, blank=True)
    deed_area_value = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    deed_area_unit = models.CharField(
        max_length=10, choices=DeedAreaUnit.choices, default=DeedAreaUnit.SQM
    )
    deed_pdf = models.FileField(
        upload_to="duediligence/deeds/%Y/%m/",
        storage=deed_pdf_storage,
        null=True,
        blank=True,
        help_text="Optional — attach to audit-trail the deed, and to enable \"Extract with AI\".",
    )

    class ExtractionStatus(models.TextChoices):
        NOT_ATTEMPTED = "not_attempted", "Not Attempted"
        COMPLETE = "complete", "Extraction Complete"
        ERROR = "error", "Extraction Failed"

    extraction_status = models.CharField(
        max_length=15, choices=ExtractionStatus.choices, default=ExtractionStatus.NOT_ATTEMPTED
    )
    extraction_error = models.TextField(blank=True)
    # Raw JSON returned by the model — audit trail / source of truth. The
    # fields copied onto this report are a convenience, not a replacement
    # for reading this if something looks off.
    extraction_raw_response = models.TextField(blank=True)
    # The model's own free-text notes on anything ambiguous, illegible, or
    # deliberately left blank rather than guessed.
    extraction_notes = models.TextField(blank=True)
    extracted_at = models.DateTimeField(null=True, blank=True)

    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.DRAFT
    )
    risk_level = models.CharField(
        max_length=10, choices=RiskLevel.choices, default=RiskLevel.GRAY
    )
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_run_error = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        khatas = sorted(
            {
                e.matched_khata_number
                for e in self.khasra_entries.all()
                if e.matched_khata_number
            }
        )
        khata_summary = ", ".join(khatas) if khatas else "?"
        return f"Title check — Khata {khata_summary} ({self.get_risk_level_display()})"


class KhasraEntry(models.Model):
    class LookupStatus(models.TextChoices):
        PENDING = "pending", "Not Yet Checked"
        FOUND = "found", "Found"
        NOT_FOUND = "not_found", "No Record Found"
        ERROR = "error", "Lookup Error"

    report = models.ForeignKey(
        TitleCheckReport, on_delete=models.CASCADE, related_name="khasra_entries"
    )
    khasra_number = models.CharField(
        max_length=20,
        help_text="e.g. 409, or 409M / 123/2 for sub-divided plots.",
    )
    order = models.PositiveSmallIntegerField(default=0)

    lookup_status = models.CharField(
        max_length=10, choices=LookupStatus.choices, default=LookupStatus.PENDING
    )
    lookup_error_message = models.TextField(blank=True)
    khata_lookup = models.ForeignKey(
        "KhataLookup",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="khasra_entries",
    )
    matched_khata_number = models.CharField(max_length=10, blank=True)

    # Denormalized snapshot of the latest relevant MutationEntry, for
    # cheap display without a join — the KhataLookup/MutationEntry rows
    # remain the audit source of truth.
    latest_mutation_case_number = models.CharField(max_length=100, blank=True)
    latest_mutation_order_date = models.DateField(null=True, blank=True)
    latest_mutation_seller_name = models.CharField(max_length=200, blank=True)
    latest_mutation_buyer_name = models.CharField(max_length=200, blank=True)
    latest_mutation_area_text = models.CharField(max_length=200, blank=True)
    latest_mutation_deed_amount_text = models.CharField(max_length=100, blank=True)
    latest_mutation_deed_date = models.DateField(null=True, blank=True)
    latest_mutation_raw_text = models.TextField(blank=True)

    # None = not comparable (e.g. nothing found, or field blank on one
    # side) — never silently coerced to True/False.
    area_match = models.BooleanField(null=True, blank=True)
    seller_match = models.BooleanField(null=True, blank=True)
    buyer_match = models.BooleanField(null=True, blank=True)

    class Meta:
        ordering = ["order", "id"]
        verbose_name_plural = "khasra entries"

    def __str__(self):
        return f"Khasra {self.khasra_number}"


class KhataLookup(models.Model):
    class FetchStatus(models.TextChoices):
        OK = "ok", "OK"
        NOT_FOUND = "not_found", "Not Found"
        ERROR = "error", "Error"

    report = models.ForeignKey(
        TitleCheckReport, on_delete=models.CASCADE, related_name="khata_lookups"
    )
    khata_number = models.CharField(max_length=10)
    unique_gata_id = models.CharField(max_length=50, blank=True)
    fetch_status = models.CharField(max_length=10, choices=FetchStatus.choices)
    fetch_error_message = models.TextField(blank=True)
    raw_report_html = models.TextField(blank=True)
    fetched_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fetched_at"]

    def __str__(self):
        return f"Khata {self.khata_number} fetch @ {self.fetched_at:%Y-%m-%d %H:%M}"


class MutationEntry(models.Model):
    khata_lookup = models.ForeignKey(
        KhataLookup, on_delete=models.CASCADE, related_name="mutation_entries"
    )
    sequence = models.PositiveSmallIntegerField(default=0)
    raw_text = models.TextField()

    case_number = models.CharField(max_length=100, blank=True)
    order_date = models.DateField(null=True, blank=True)
    khata_number_in_entry = models.CharField(max_length=10, blank=True)
    khasra_numbers_in_entry = models.CharField(max_length=100, blank=True)
    area_text = models.CharField(max_length=200, blank=True)
    seller_name = models.CharField(max_length=200, blank=True)
    buyer_name = models.CharField(max_length=200, blank=True)
    deed_amount_text = models.CharField(max_length=100, blank=True)
    deed_date = models.DateField(null=True, blank=True)

    is_litigation_flagged = models.BooleanField(default=False)
    matched_litigation_keywords = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["sequence"]

    def __str__(self):
        return self.case_number or f"Entry #{self.sequence}"
