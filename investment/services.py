"""Phase 2: document upload/management. Validation, checksum computation,
and duplicate detection live here rather than in admin.py, so a future
non-admin upload path (Phase 23's API, or a public intake form) reuses the
exact same rules — mirrors duediligence/services.py's role as the one
place that touches both file bytes and the DB row.

Phase 3: analyze_document() is the extraction-pipeline entrypoint —
Upload/Validate/Store already happened (Phase 2); this does Extract →
Analyze → Store structured findings → Generate evidence, all via one AI
call (see document_ai.py for why there's no separate text-extraction
step). Never raises to the caller, matching duediligence.services'
existing contract for AI-calling entrypoints.
"""

import hashlib

from django.conf import settings
from django.utils import timezone

from . import document_ai
from .models import DocumentAnalysis, Evidence, PropertyDocument


def compute_checksum(file) -> str:
    """SHA-256 hex digest of the file's current contents. Leaves the
    file's read position at 0 afterwards so a caller can still read/save
    it normally afterwards (mirrors duediligence.services.extract_deed's
    care around not consuming a file object other code still needs)."""
    file.seek(0)
    digest = hashlib.sha256()
    chunks = file.chunks() if hasattr(file, "chunks") else iter(lambda: file.read(8192), b"")
    for chunk in chunks:
        digest.update(chunk)
    file.seek(0)
    return digest.hexdigest()


def find_duplicate_document(property_listing, checksum: str, exclude_pk=None):
    """Same file content already uploaded for this property — surfaced as
    a warning, never auto-rejected or auto-deleted: a duplicate upload
    might be a genuine mistake worth flagging, but only a human should
    decide whether to remove it (same restraint this system's product
    principles require of data-quality checks generally)."""
    if not checksum:
        return None
    qs = PropertyDocument.objects.filter(property_listing=property_listing, checksum=checksum)
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)
    return qs.first()


# schema field -> human-readable claim label, for turning a
# DocumentExtractionSchema result into individual Evidence rows. Deliberately
# excludes document_type_detected (handled separately, as a mismatch flag,
# not a property claim), inconsistencies_noted/missing_information (stored
# in DocumentAnalysis for a later due-diligence/risk pass to read, not
# claims in their own right), and confidence_notes (free-text meta).
_EVIDENCE_FIELD_LABELS = (
    ("owner_or_seller_names", "Owner/Seller"),
    ("buyer_names", "Buyer"),
    ("nominator_names", "Nominator"),
    ("khasra_numbers", "Khasra Number(s)"),
    ("khata_number", "Khata Number"),
    ("survey_number", "Survey Number"),
    ("property_address", "Property Address"),
    ("land_classification", "Land Classification"),
    ("transaction_price", "Transaction Price"),
    ("document_dates", "Document Date(s)"),
    ("developer_or_project_name", "Developer/Project"),
    ("mentioned_approvals", "Mentioned Approval(s)"),
    ("mentioned_nocs", "Mentioned NOC(s)"),
    ("restrictions", "Restriction(s)"),
)


def _evidence_value_text(value) -> str:
    if isinstance(value, list):
        return "; ".join(str(v) for v in value if v)
    return str(value)


def _create_evidence_from_extraction(document: PropertyDocument, extracted: document_ai.DocumentExtractionSchema) -> None:
    """Every extracted fact becomes its own traceable Evidence row — the
    core anti-hallucination mechanism (Phase 5): a later report/AI-analyst
    pass may only state what has a row here, never invent from nothing."""
    area_value = extracted.property_area
    if area_value:
        area_text = f"{area_value} {extracted.property_area_unit}".strip() if extracted.property_area_unit else area_value
        Evidence.objects.create(
            property_listing=document.property_listing,
            analysis_run=document.analysis_run,
            claim=f"Property Area: {area_text}",
            source_type=Evidence.SourceType.FACT,
            source_document=document,
            extracted_value=area_text,
        )

    for field_name, label in _EVIDENCE_FIELD_LABELS:
        value = getattr(extracted, field_name)
        if not value:
            continue
        value_text = _evidence_value_text(value)
        Evidence.objects.create(
            property_listing=document.property_listing,
            analysis_run=document.analysis_run,
            claim=f"{label}: {value_text}",
            source_type=Evidence.SourceType.FACT,
            source_document=document,
            extracted_value=value_text,
        )

    declared = document.get_document_type_display()
    detected = extracted.document_type_detected
    # declared ("Khasra") should appear WITHIN the AI's freer description
    # ("khasra extract") when they agree — not the other way round, since
    # detected is typically longer/more specific than the fixed label.
    if detected and declared.lower() not in detected.strip().lower():
        Evidence.objects.create(
            property_listing=document.property_listing,
            analysis_run=document.analysis_run,
            claim=f'AI read this document as "{detected}", not the declared type "{declared}" — worth checking.',
            source_type=Evidence.SourceType.AI_ANALYSIS,
            source_document=document,
            extracted_value=detected,
        )


def analyze_document(document: PropertyDocument) -> None:
    """The entrypoint a "Analyze with AI" admin action calls. Never raises
    to the caller — mirrors duediligence.services.extract_deed's defensive
    shape. Re-running replaces the previous DocumentAnalysis and its
    Evidence rows rather than accumulating duplicates, since a re-run
    means "trust this result instead," not "add a second opinion"."""
    if not document.file:
        analysis, _ = DocumentAnalysis.objects.get_or_create(document=document)
        analysis.error_message = "No file uploaded."
        analysis.analyzed_at = timezone.now()
        analysis.save()
        document.verification_status = PropertyDocument.VerificationStatus.NEEDS_REVIEW
        document.save(update_fields=["verification_status"])
        return

    document.verification_status = PropertyDocument.VerificationStatus.PROCESSING
    document.save(update_fields=["verification_status"])

    try:
        with document.file.open("rb") as f:
            file_bytes = f.read()
    except Exception as exc:  # noqa: BLE001 — storage backend (Cloudinary in
        # production) can fail in ways specific to that backend; this must
        # never raise to the caller, matching this function's own contract.
        analysis, _ = DocumentAnalysis.objects.get_or_create(document=document)
        analysis.error_message = f"Could not read the file: {exc.__class__.__name__}: {exc}"
        analysis.analyzed_at = timezone.now()
        analysis.save()
        document.verification_status = PropertyDocument.VerificationStatus.NEEDS_REVIEW
        document.save(update_fields=["verification_status"])
        return

    try:
        extracted, raw_json = document_ai.extract_document_fields(file_bytes, document.file.name)
    except document_ai.DocumentExtractionError as exc:
        analysis, _ = DocumentAnalysis.objects.get_or_create(document=document)
        analysis.error_message = str(exc)
        analysis.analyzed_at = timezone.now()
        analysis.save()
        document.verification_status = PropertyDocument.VerificationStatus.NEEDS_REVIEW
        document.save(update_fields=["verification_status"])
        return

    # Re-running: clear the previous pass's Evidence rows for this document
    # before generating a fresh set, so they don't silently double up.
    Evidence.objects.filter(source_document=document, source_type__in=[
        Evidence.SourceType.FACT, Evidence.SourceType.AI_ANALYSIS,
    ]).delete()

    analysis, _ = DocumentAnalysis.objects.update_or_create(
        document=document,
        defaults={
            "extracted_data": extracted.model_dump(),
            "raw_ai_response": raw_json,
            "extraction_method": f"ai:{settings.AI_EXTRACTION_PROVIDER}",
            "flagged_inconsistencies": "; ".join(extracted.inconsistencies_noted),
            "error_message": "",
            "analyzed_at": timezone.now(),
        },
    )
    _create_evidence_from_extraction(document, extracted)

    document.verification_status = PropertyDocument.VerificationStatus.ANALYZED
    document.save(update_fields=["verification_status"])
