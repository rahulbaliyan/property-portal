"""Phase 1 tests: the domain model skeleton itself — creation, key
relationships, and the constraints that matter most (UNKNOWN must be a
real, distinct status; one check/factor per category per parent).

Phase 2 tests: document upload validation (services.py + models.py's
validate_document_file), checksum computation, duplicate detection, and
the admin wiring that fixed a real Phase-1 bug (inline-added
PropertyDocument/PropertyComparable rows had no way to get
property_listing populated, since it's a second FK the inline form never
shows).
"""

import hashlib
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from properties.models import Property

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
    validate_document_file,
    PropertyScore,
    PropertyValuation,
    ScoringConfiguration,
    ScoringFactor,
    VerificationItem,
)


def _make_property(**overrides):
    defaults = dict(
        title="Test Plot",
        property_type=Property.PropertyType.PLOT,
        region=Property.Region.DEHRADUN,
        area_value=Decimal("100"),
    )
    defaults.update(overrides)
    return Property.objects.create(**defaults)


class AnalysisRunTests(TestCase):
    def test_create_run_defaults_to_pending(self):
        prop = _make_property()
        run = AnalysisRun.objects.create(property_listing=prop)
        self.assertEqual(run.status, AnalysisRun.Status.PENDING)
        self.assertIn("Analysis run", str(run))

    def test_run_deleted_when_property_deleted(self):
        prop = _make_property()
        run = AnalysisRun.objects.create(property_listing=prop)
        prop.delete()
        self.assertFalse(AnalysisRun.objects.filter(pk=run.pk).exists())


class DueDiligenceCheckTests(TestCase):
    def setUp(self):
        self.run = AnalysisRun.objects.create(property_listing=_make_property())

    def test_default_status_is_unknown_not_pass(self):
        check = DueDiligenceCheck.objects.create(
            analysis_run=self.run, category=DueDiligenceCheck.Category.OWNERSHIP
        )
        self.assertEqual(check.status, DueDiligenceCheck.Status.UNKNOWN)
        self.assertNotEqual(check.status, DueDiligenceCheck.Status.PASS)

    def test_one_check_per_category_per_run(self):
        DueDiligenceCheck.objects.create(
            analysis_run=self.run, category=DueDiligenceCheck.Category.OWNERSHIP
        )
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                DueDiligenceCheck.objects.create(
                    analysis_run=self.run, category=DueDiligenceCheck.Category.OWNERSHIP
                )

    def test_same_category_allowed_on_a_different_run(self):
        other_run = AnalysisRun.objects.create(property_listing=_make_property(title="Other"))
        DueDiligenceCheck.objects.create(
            analysis_run=self.run, category=DueDiligenceCheck.Category.OWNERSHIP
        )
        # Must not raise — the uniqueness is scoped per-run, not global.
        DueDiligenceCheck.objects.create(
            analysis_run=other_run, category=DueDiligenceCheck.Category.OWNERSHIP
        )


class EvidenceTests(TestCase):
    def test_evidence_links_to_property_and_optional_document(self):
        prop = _make_property()
        run = AnalysisRun.objects.create(property_listing=prop)
        document = PropertyDocument.objects.create(
            property_listing=prop,
            analysis_run=run,
            document_type=PropertyDocument.DocumentType.SALE_DEED,
        )
        evidence = Evidence.objects.create(
            property_listing=prop,
            analysis_run=run,
            claim="Property area is 150 gaj",
            source_type=Evidence.SourceType.FACT,
            source_document=document,
            page_number=3,
        )
        self.assertEqual(evidence.verification_status, Evidence.VerificationStatus.UNVERIFIED)
        self.assertEqual(evidence.source_document, document)

    def test_evidence_survives_without_a_document(self):
        # An estimate (e.g. a market-price calculation) has no source
        # document at all — must not be required.
        prop = _make_property()
        evidence = Evidence.objects.create(
            property_listing=prop,
            claim="Estimated market price is ₹23,000/gaj",
            source_type=Evidence.SourceType.ESTIMATE,
        )
        self.assertIsNone(evidence.source_document)


class PropertyRiskTests(TestCase):
    def test_risk_optionally_links_to_evidence(self):
        prop = _make_property()
        run = AnalysisRun.objects.create(property_listing=prop)
        evidence = Evidence.objects.create(
            property_listing=prop,
            analysis_run=run,
            claim="Area differs between listing and sale deed",
            source_type=Evidence.SourceType.FACT,
        )
        risk = PropertyRisk.objects.create(
            analysis_run=run,
            category=PropertyRisk.Category.DOCUMENT,
            severity=PropertyRisk.Severity.HIGH,
            reason="Property area differs between listing and sale deed.",
            recommended_verification="Verify the legally transferable area before proceeding.",
            evidence=evidence,
        )
        self.assertEqual(risk.evidence, evidence)


class ScoringConfigurationTests(TestCase):
    def test_factor_weights_scoped_per_configuration(self):
        config = ScoringConfiguration.objects.create(name="Default")
        ScoringFactor.objects.create(
            configuration=config,
            category=ScoringFactor.Category.LEGAL_DOCUMENTATION,
            weight_percent=Decimal("30"),
        )
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                ScoringFactor.objects.create(
                    configuration=config,
                    category=ScoringFactor.Category.LEGAL_DOCUMENTATION,
                    weight_percent=Decimal("40"),
                )

    def test_same_category_allowed_in_a_different_configuration(self):
        config_a = ScoringConfiguration.objects.create(name="A")
        config_b = ScoringConfiguration.objects.create(name="B")
        ScoringFactor.objects.create(
            configuration=config_a,
            category=ScoringFactor.Category.LEGAL_DOCUMENTATION,
            weight_percent=Decimal("30"),
        )
        ScoringFactor.objects.create(
            configuration=config_b,
            category=ScoringFactor.Category.LEGAL_DOCUMENTATION,
            weight_percent=Decimal("25"),
        )


class PropertyScoreAndValuationTests(TestCase):
    def test_score_and_valuation_are_one_per_run(self):
        run = AnalysisRun.objects.create(property_listing=_make_property())
        PropertyScore.objects.create(analysis_run=run, overall_score=76, confidence_score=61)
        PropertyValuation.objects.create(analysis_run=run, comparable_count=5)

        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                PropertyScore.objects.create(analysis_run=run, overall_score=50)

    def test_missing_data_penalty_defaults_false(self):
        run = AnalysisRun.objects.create(property_listing=_make_property())
        score = PropertyScore.objects.create(analysis_run=run)
        self.assertFalse(score.missing_data_penalty_applied)


class VerificationItemTests(TestCase):
    def test_defaults_to_pending_and_required(self):
        run = AnalysisRun.objects.create(property_listing=_make_property())
        item = VerificationItem.objects.create(
            analysis_run=run, description="Verify ownership chain via Bhulekh"
        )
        self.assertEqual(item.status, VerificationItem.Status.PENDING)
        self.assertTrue(item.is_required)


class PropertyComparableTests(TestCase):
    def test_unverified_transaction_is_the_default(self):
        prop = _make_property()
        comparable = PropertyComparable.objects.create(
            property_listing=prop,
            area_value=Decimal("120"),
            area_unit=Property.AreaUnit.GAJ,
            price=Decimal("2500000"),
        )
        self.assertFalse(comparable.is_verified_transaction)


class DocumentAnalysisTests(TestCase):
    def test_one_analysis_per_document(self):
        prop = _make_property()
        document = PropertyDocument.objects.create(
            property_listing=prop, document_type=PropertyDocument.DocumentType.KHASRA
        )
        DocumentAnalysis.objects.create(document=document)
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                DocumentAnalysis.objects.create(document=document)


class FieldOverrideLogTests(TestCase):
    def test_records_who_changed_what(self):
        run = AnalysisRun.objects.create(property_listing=_make_property())
        user = get_user_model().objects.create_user(username="reviewer", password="pw")
        log = FieldOverrideLog.objects.create(
            analysis_run=run,
            field_name="seller_name",
            previous_value="",
            new_value="Ramesh Kumar",
            changed_by=user,
            reason="AI misread the seller's name from a low-quality scan.",
        )
        self.assertIn("seller_name", str(log))
        self.assertEqual(log.changed_by, user)


class AdminRegistrationTests(TestCase):
    """Every model should be reachable from admin from day one, even
    before any custom behavior exists on top of the plain CRUD."""

    def setUp(self):
        get_user_model().objects.create_superuser(
            username="staffer", password="pw", email="staffer@example.com"
        )
        self.client.login(username="staffer", password="pw")

    def test_analysis_run_changelist_loads(self):
        response = self.client.get("/admin/investment/analysisrun/")
        self.assertEqual(response.status_code, 200)

    def test_scoring_configuration_changelist_loads(self):
        response = self.client.get("/admin/investment/scoringconfiguration/")
        self.assertEqual(response.status_code, 200)

    def test_property_document_changelist_loads(self):
        response = self.client.get("/admin/investment/propertydocument/")
        self.assertEqual(response.status_code, 200)


def _pdf_file(name="deed.pdf", content=b"%PDF-1.4 fake content"):
    return SimpleUploadedFile(name, content, content_type="application/pdf")


class DocumentFileValidationTests(TestCase):
    def test_disallowed_extension_rejected(self):
        with self.assertRaises(ValidationError):
            validate_document_file(SimpleUploadedFile("deed.exe", b"x", content_type="application/octet-stream"))

    def test_allowed_extensions_pass(self):
        for name in ("deed.pdf", "deed.PDF", "scan.jpg", "scan.jpeg", "scan.png"):
            validate_document_file(SimpleUploadedFile(name, b"x"))  # must not raise

    def test_oversized_file_rejected(self):
        from investment.models import MAX_DOCUMENT_UPLOAD_BYTES

        oversized = SimpleUploadedFile("deed.pdf", b"x" * (MAX_DOCUMENT_UPLOAD_BYTES + 1))
        with self.assertRaises(ValidationError):
            validate_document_file(oversized)


class ChecksumAndDuplicateDetectionTests(TestCase):
    def test_checksum_matches_known_sha256(self):
        content = b"deterministic content for checksum test"
        file = SimpleUploadedFile("deed.pdf", content)
        self.assertEqual(services.compute_checksum(file), hashlib.sha256(content).hexdigest())

    def test_checksum_leaves_file_readable_afterwards(self):
        content = b"content still readable after checksum"
        file = SimpleUploadedFile("deed.pdf", content)
        services.compute_checksum(file)
        self.assertEqual(file.read(), content)

    def test_no_duplicate_when_checksum_blank(self):
        prop = _make_property()
        self.assertIsNone(services.find_duplicate_document(prop, ""))

    def test_finds_duplicate_by_checksum_within_same_property(self):
        prop = _make_property()
        existing = PropertyDocument.objects.create(
            property_listing=prop,
            document_type=PropertyDocument.DocumentType.SALE_DEED,
            file=_pdf_file(),
            checksum="abc123",
        )
        duplicate = services.find_duplicate_document(prop, "abc123")
        self.assertEqual(duplicate, existing)

    def test_same_checksum_on_a_different_property_is_not_a_duplicate(self):
        prop_a = _make_property(title="A")
        prop_b = _make_property(title="B")
        PropertyDocument.objects.create(
            property_listing=prop_a,
            document_type=PropertyDocument.DocumentType.SALE_DEED,
            file=_pdf_file(),
            checksum="abc123",
        )
        self.assertIsNone(services.find_duplicate_document(prop_b, "abc123"))

    def test_excludes_self_when_checking_for_duplicates(self):
        prop = _make_property()
        doc = PropertyDocument.objects.create(
            property_listing=prop,
            document_type=PropertyDocument.DocumentType.SALE_DEED,
            file=_pdf_file(),
            checksum="abc123",
        )
        self.assertIsNone(services.find_duplicate_document(prop, "abc123", exclude_pk=doc.pk))


class PropertyDocumentAdminUploadTests(TestCase):
    """Covers the Phase 2 admin wiring: checksum/uploaded_by computed on
    save, and a duplicate upload surfaced as a warning (never blocked)."""

    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="staffer", password="pw", email="staffer@example.com"
        )
        self.client.login(username="staffer", password="pw")
        self.property = _make_property()

    def test_add_via_admin_sets_checksum_and_uploaded_by(self):
        url = reverse("admin:investment_propertydocument_add")
        response = self.client.post(
            url,
            {
                "property_listing": self.property.pk,
                "document_type": PropertyDocument.DocumentType.SALE_DEED,
                "file": _pdf_file(),
                "verification_status": PropertyDocument.VerificationStatus.UPLOADED,
                "metadata": "{}",
                "analysis-0-TOTAL_FORMS": "0",
                "analysis-0-INITIAL_FORMS": "0",
                "analysis-TOTAL_FORMS": "0",
                "analysis-INITIAL_FORMS": "0",
                "analysis-MIN_NUM_FORMS": "0",
                "analysis-MAX_NUM_FORMS": "1",
            },
        )
        self.assertEqual(response.status_code, 302, response.context["adminform"].form.errors if response.status_code == 200 else None)
        document = PropertyDocument.objects.get(property_listing=self.property)
        self.assertTrue(document.checksum)
        self.assertEqual(document.uploaded_by, self.user)

    def test_duplicate_upload_warns_without_blocking(self):
        existing_checksum = hashlib.sha256(b"same content").hexdigest()
        PropertyDocument.objects.create(
            property_listing=self.property,
            document_type=PropertyDocument.DocumentType.SALE_DEED,
            file=_pdf_file("first.pdf", b"same content"),
            checksum=existing_checksum,
        )

        url = reverse("admin:investment_propertydocument_add")
        response = self.client.post(
            url,
            {
                "property_listing": self.property.pk,
                "document_type": PropertyDocument.DocumentType.KHASRA,
                "file": _pdf_file("second.pdf", b"same content"),
                "verification_status": PropertyDocument.VerificationStatus.UPLOADED,
                "metadata": "{}",
                "analysis-0-TOTAL_FORMS": "0",
                "analysis-0-INITIAL_FORMS": "0",
                "analysis-TOTAL_FORMS": "0",
                "analysis-INITIAL_FORMS": "0",
                "analysis-MIN_NUM_FORMS": "0",
                "analysis-MAX_NUM_FORMS": "1",
            },
            follow=True,
        )
        messages = [str(m) for m in response.context["messages"]]
        self.assertTrue(any("identical content" in m for m in messages))
        self.assertEqual(PropertyDocument.objects.filter(property_listing=self.property).count(), 2)


class AnalysisRunInlineDocumentTests(TestCase):
    """Regression test for the Phase-1 bug fixed in Phase 2: adding a
    PropertyDocument/PropertyComparable via AnalysisRun's inline must
    auto-fill property_listing from the parent run, since the inline form
    never shows that field."""

    def setUp(self):
        get_user_model().objects.create_superuser(
            username="staffer", password="pw", email="staffer@example.com"
        )
        self.client.login(username="staffer", password="pw")
        self.property = _make_property()
        self.run = AnalysisRun.objects.create(property_listing=self.property)

    def _inline_management_form(self, prefix, total=0, initial=0):
        return {
            f"{prefix}-TOTAL_FORMS": str(total),
            f"{prefix}-INITIAL_FORMS": str(initial),
            f"{prefix}-MIN_NUM_FORMS": "0",
            f"{prefix}-MAX_NUM_FORMS": "1000",
        }

    def _base_payload(self):
        payload = {"property_listing": self.property.pk, "status": AnalysisRun.Status.PENDING}
        for prefix in (
            "due_diligence_checks",
            "risks",
            "verification_items",
            "documents",
            "comparables",
            "valuation",
            "score",
            "override_logs",
        ):
            payload.update(self._inline_management_form(prefix))
        return payload

    def test_document_added_via_inline_gets_property_listing(self):
        payload = self._base_payload()
        payload.update(
            {
                "documents-TOTAL_FORMS": "1",
                "documents-0-document_type": PropertyDocument.DocumentType.SALE_DEED,
                "documents-0-file": _pdf_file(),
                "documents-0-verification_status": PropertyDocument.VerificationStatus.UPLOADED,
            }
        )
        url = reverse("admin:investment_analysisrun_change", args=[self.run.pk])
        response = self.client.post(url, payload)
        self.assertEqual(
            response.status_code, 302, response.context["errors"] if response.status_code == 200 else None
        )
        document = PropertyDocument.objects.get(analysis_run=self.run)
        self.assertEqual(document.property_listing, self.property)
        self.assertTrue(document.checksum)

    def test_comparable_added_via_inline_gets_property_listing(self):
        payload = self._base_payload()
        payload.update(
            {
                "comparables-TOTAL_FORMS": "1",
                "comparables-0-area_value": "120",
                "comparables-0-area_unit": Property.AreaUnit.GAJ,
                "comparables-0-price": "2500000",
            }
        )
        url = reverse("admin:investment_analysisrun_change", args=[self.run.pk])
        response = self.client.post(url, payload)
        self.assertEqual(
            response.status_code, 302, response.context["errors"] if response.status_code == 200 else None
        )
        comparable = PropertyComparable.objects.get(analysis_run=self.run)
        self.assertEqual(comparable.property_listing, self.property)
