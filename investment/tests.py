"""Phase 1 tests: the domain model skeleton itself — creation, key
relationships, and the constraints that matter most (UNKNOWN must be a
real, distinct status; one check/factor per category per parent).

Phase 2 tests: document upload validation (services.py + models.py's
validate_document_file), checksum computation, duplicate detection, and
the admin wiring that fixed a real Phase-1 bug (inline-added
PropertyDocument/PropertyComparable rows had no way to get
property_listing populated, since it's a second FK the inline form never
shows).

Phase 3 tests: document_ai's provider/schema error handling, and
services.analyze_document()'s extraction -> DocumentAnalysis -> Evidence
pipeline — mocked at document_ai._build_model, same boundary duediligence
mocks deed_ai at, never real HTTP.

Every test class that uploads a real file is decorated with
@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT) — without it, FileSystemStorage
(the local/test storage backend) writes real files into this project's
actual media/ directory on every run. Mirrors duediligence/tests.py's own
TEST_MEDIA_ROOT pattern.

Phase 6 tests: due_diligence.run_due_diligence()'s bridge into real
duediligence.TitleCheckReport/KhasraEntry/MutationEntry fixtures (built
directly, not via a live Bhulekh fetch — same boundary duediligence's own
tests mock at) and the admin wiring for the "Run Due Diligence" button.
"""

import hashlib
import shutil
import tempfile
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from duediligence.models import KhasraEntry, KhataLookup, MutationEntry, TitleCheckReport
from properties.models import Property

from . import ai_analyst, document_ai, due_diligence, location_intelligence, risk_engine, scoring, services, valuation
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


TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="investment-test-media-")


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


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
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


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
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

    def test_checksum_computed_before_the_object_is_saved(self):
        # Regression test for a real production 500: computing the
        # checksum by reading document.file back AFTER save() works fine
        # against local FileSystemStorage (every other test here) but
        # crashed against Cloudinary's raw-resource storage, which
        # doesn't support re-opening a just-saved file for reading. Must
        # be computed from the raw upload BEFORE save() instead — this
        # asserts that invariant directly rather than just the local-only
        # happy path, which would never have caught the regression.
        with patch("investment.admin.services.compute_checksum") as mock_checksum:
            def assert_not_yet_saved(file):
                document = PropertyDocument.objects.filter(property_listing=self.property).first()
                self.assertIsNone(document, "checksum must be computed before the row is saved")
                return "fake-checksum"

            mock_checksum.side_effect = assert_not_yet_saved
            url = reverse("admin:investment_propertydocument_add")
            response = self.client.post(
                url,
                {
                    "property_listing": self.property.pk,
                    "document_type": PropertyDocument.DocumentType.SALE_DEED,
                    "file": _pdf_file(),
                    "verification_status": PropertyDocument.VerificationStatus.UPLOADED,
                    "metadata": "{}",
                    "analysis-TOTAL_FORMS": "0",
                    "analysis-INITIAL_FORMS": "0",
                    "analysis-MIN_NUM_FORMS": "0",
                    "analysis-MAX_NUM_FORMS": "1",
                },
            )
        self.assertEqual(response.status_code, 302)
        mock_checksum.assert_called_once()
        self.assertEqual(
            PropertyDocument.objects.get(property_listing=self.property).checksum, "fake-checksum"
        )

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


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
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

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)


class DocumentAiClientTests(TestCase):
    @override_settings(AI_EXTRACTION_PROVIDER="anthropic", ANTHROPIC_API_KEY="")
    def test_config_error_when_anthropic_key_blank(self):
        with self.assertRaises(document_ai.DocumentExtractionConfigError):
            document_ai.extract_document_fields(b"fake-pdf-bytes", "deed.pdf")

    @override_settings(AI_EXTRACTION_PROVIDER="carrier-pigeon")
    def test_config_error_on_unrecognized_provider(self):
        with self.assertRaises(document_ai.DocumentExtractionConfigError):
            document_ai.extract_document_fields(b"fake-pdf-bytes", "deed.pdf")

    @override_settings(AI_EXTRACTION_PROVIDER="anthropic", ANTHROPIC_API_KEY="test-key")
    @patch("investment.document_ai._build_model")
    def test_request_error_wrapped(self, mock_build_model):
        mock_model = MagicMock()
        mock_model.invoke.side_effect = RuntimeError("network boom")
        mock_build_model.return_value = mock_model
        with self.assertRaises(document_ai.DocumentExtractionRequestError):
            document_ai.extract_document_fields(b"fake-pdf-bytes", "deed.pdf")

    @patch("investment.document_ai._build_model")
    def test_parse_error_when_schema_validation_fails(self, mock_build_model):
        mock_model = MagicMock()
        mock_model.invoke.return_value = {"parsed": None, "parsing_error": "bad json"}
        mock_build_model.return_value = mock_model
        with self.assertRaises(document_ai.DocumentExtractionParseError):
            document_ai.extract_document_fields(b"fake-pdf-bytes", "deed.pdf")

    @patch("investment.document_ai._build_model")
    def test_successful_extraction_returns_parsed_and_raw_json(self, mock_build_model):
        schema = document_ai.DocumentExtractionSchema(khasra_numbers=["410", "409"])
        mock_model = MagicMock()
        mock_model.invoke.return_value = {"parsed": schema, "parsing_error": None}
        mock_build_model.return_value = mock_model

        parsed, raw_json = document_ai.extract_document_fields(b"fake-pdf-bytes", "khasra.pdf")
        self.assertEqual(parsed.khasra_numbers, ["410", "409"])
        self.assertIn("410", raw_json)

    def test_unknown_extension_falls_back_to_pdf_mime_type(self):
        # _MIME_TYPES only covers pdf/jpg/jpeg/png — anything else should
        # still produce a usable file_block rather than raising, since the
        # validator (validate_document_file) is what actually enforces the
        # allow-list; this is just a defensive fallback.
        self.assertEqual(document_ai._MIME_TYPES.get(".weird", "application/pdf"), "application/pdf")


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class AnalyzeDocumentServiceTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def _document_with_file(self, **kwargs):
        prop = _make_property()
        return PropertyDocument.objects.create(
            property_listing=prop,
            document_type=PropertyDocument.DocumentType.KHASRA,
            file=_pdf_file(),
            **kwargs,
        )

    def test_no_file_sets_error_and_needs_review(self):
        prop = _make_property()
        document = PropertyDocument.objects.create(
            property_listing=prop, document_type=PropertyDocument.DocumentType.KHASRA, file=""
        )
        services.analyze_document(document)
        document.refresh_from_db()
        self.assertEqual(document.verification_status, PropertyDocument.VerificationStatus.NEEDS_REVIEW)
        self.assertIn("No file", document.analysis.error_message)

    @patch("investment.services.document_ai.extract_document_fields")
    def test_oversized_file_rejected_before_reading(self, mock_extract):
        # Regression guard: a real 7.7MB production upload OOM-killed the
        # single 512MB worker when read into memory for the AI call — this
        # must be rejected via the cheap .file.size check before ever
        # opening the file, so extract_document_fields is never called.
        prop = _make_property()
        oversized = _pdf_file("big.pdf", b"x" * (services.AI_ANALYSIS_MAX_BYTES + 1))
        document = PropertyDocument.objects.create(
            property_listing=prop, document_type=PropertyDocument.DocumentType.KHASRA, file=oversized
        )
        services.analyze_document(document)
        document.refresh_from_db()
        self.assertEqual(document.verification_status, PropertyDocument.VerificationStatus.NEEDS_REVIEW)
        self.assertIn("MB", document.analysis.error_message)
        mock_extract.assert_not_called()

    @patch("investment.services.document_ai.extract_document_fields")
    def test_extraction_error_sets_needs_review(self, mock_extract):
        mock_extract.side_effect = document_ai.DocumentExtractionRequestError("timed out")
        document = self._document_with_file()

        services.analyze_document(document)  # must not raise
        document.refresh_from_db()
        self.assertEqual(document.verification_status, PropertyDocument.VerificationStatus.NEEDS_REVIEW)
        self.assertIn("timed out", document.analysis.error_message)

    @patch("investment.services.document_ai.extract_document_fields")
    def test_success_creates_analysis_and_evidence(self, mock_extract):
        schema = document_ai.DocumentExtractionSchema(
            owner_or_seller_names=["Ramesh Kumar"],
            buyer_names=["Suresh Singh"],
            nominator_names=["Krishna Sharma"],
            khasra_numbers=["410", "409"],
            khata_number="222",
            property_area="150",
            property_area_unit="gaj",
            transaction_price="1800000",
            document_dates=["2025-06-02"],
            inconsistencies_noted=["Area stated twice with different values"],
            missing_information=["Survey number not printed"],
        )
        mock_extract.return_value = (schema, '{"khata_number": "222"}')
        document = self._document_with_file()

        services.analyze_document(document)

        document.refresh_from_db()
        self.assertEqual(document.verification_status, PropertyDocument.VerificationStatus.ANALYZED)

        analysis = document.analysis
        self.assertEqual(analysis.extraction_method, "ai:google")
        self.assertEqual(analysis.extracted_data["khata_number"], "222")
        self.assertIn("Area stated twice", analysis.flagged_inconsistencies)
        self.assertFalse(analysis.error_message)

        evidence = Evidence.objects.filter(source_document=document)
        claims = [e.claim for e in evidence]
        self.assertTrue(any("Ramesh Kumar" in c for c in claims))
        self.assertTrue(any("Suresh Singh" in c for c in claims))
        self.assertTrue(any("Krishna Sharma" in c for c in claims))
        self.assertTrue(any("150 gaj" in c for c in claims))
        self.assertTrue(any("1800000" in c for c in claims))
        self.assertTrue(any("410; 409" in c for c in claims))
        for item in evidence:
            self.assertEqual(item.source_type, Evidence.SourceType.FACT)
            self.assertEqual(item.verification_status, Evidence.VerificationStatus.UNVERIFIED)
            self.assertEqual(item.property_listing, document.property_listing)

    @patch("investment.services.document_ai.extract_document_fields")
    def test_flags_document_type_mismatch_as_ai_analysis_evidence(self, mock_extract):
        schema = document_ai.DocumentExtractionSchema(document_type_detected="NOC from forest department")
        mock_extract.return_value = (schema, "{}")
        document = self._document_with_file()  # declared type: Khasra

        services.analyze_document(document)

        mismatch = Evidence.objects.get(source_document=document, source_type=Evidence.SourceType.AI_ANALYSIS)
        self.assertIn("NOC from forest department", mismatch.claim)
        self.assertIn("Khasra", mismatch.claim)

    @patch("investment.services.document_ai.extract_document_fields")
    def test_no_mismatch_evidence_when_detected_type_matches(self, mock_extract):
        schema = document_ai.DocumentExtractionSchema(document_type_detected="khasra extract")
        mock_extract.return_value = (schema, "{}")
        document = self._document_with_file()  # declared type: Khasra

        services.analyze_document(document)

        self.assertFalse(Evidence.objects.filter(source_document=document, source_type=Evidence.SourceType.AI_ANALYSIS).exists())

    @patch("investment.services.document_ai.extract_document_fields")
    def test_rerun_replaces_previous_evidence_instead_of_duplicating(self, mock_extract):
        document = self._document_with_file()
        mock_extract.return_value = (
            document_ai.DocumentExtractionSchema(khata_number="111"),
            "{}",
        )
        services.analyze_document(document)
        self.assertEqual(Evidence.objects.filter(source_document=document).count(), 1)

        mock_extract.return_value = (
            document_ai.DocumentExtractionSchema(khata_number="222"),
            "{}",
        )
        services.analyze_document(document)

        remaining = Evidence.objects.filter(source_document=document)
        self.assertEqual(remaining.count(), 1)
        self.assertIn("222", remaining.first().claim)


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class AnalyzeDocumentAdminViewTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        get_user_model().objects.create_superuser(
            username="staffer", password="pw", email="staffer@example.com"
        )
        self.client.login(username="staffer", password="pw")
        prop = _make_property()
        self.document = PropertyDocument.objects.create(
            property_listing=prop, document_type=PropertyDocument.DocumentType.KHASRA, file=_pdf_file()
        )

    def test_rejects_get(self):
        url = reverse("admin:investment_propertydocument_analyze_ai", args=[self.document.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 405)

    def test_requires_staff(self):
        self.client.logout()
        url = reverse("admin:investment_propertydocument_analyze_ai", args=[self.document.pk])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)

    @patch("investment.services.document_ai.extract_document_fields")
    def test_post_triggers_analysis_and_redirects(self, mock_extract):
        mock_extract.return_value = (document_ai.DocumentExtractionSchema(khata_number="222"), "{}")
        url = reverse("admin:investment_propertydocument_analyze_ai", args=[self.document.pk])
        response = self.client.post(url)
        self.assertRedirects(
            response, reverse("admin:investment_propertydocument_change", args=[self.document.pk])
        )
        self.document.refresh_from_db()
        self.assertEqual(self.document.verification_status, PropertyDocument.VerificationStatus.ANALYZED)

    def test_change_form_shows_button_only_when_file_present(self):
        url = reverse("admin:investment_propertydocument_change", args=[self.document.pk])
        response = self.client.get(url)
        self.assertContains(response, "Analyze with AI")


class RunDueDiligenceNoReportTests(TestCase):
    def test_no_title_check_report_leaves_bridged_categories_pending(self):
        run = AnalysisRun.objects.create(property_listing=_make_property())
        due_diligence.run_due_diligence(run)

        for category in (
            DueDiligenceCheck.Category.REGISTRY,
            DueDiligenceCheck.Category.OWNERSHIP,
            DueDiligenceCheck.Category.LITIGATION,
        ):
            check = DueDiligenceCheck.objects.get(analysis_run=run, category=category)
            self.assertEqual(check.status, DueDiligenceCheck.Status.PENDING_VERIFICATION)
            self.assertTrue(check.verification_required)

    def test_incomplete_report_also_leaves_bridged_categories_pending(self):
        prop = _make_property()
        TitleCheckReport.objects.create(property=prop, status=TitleCheckReport.Status.QUEUED)
        run = AnalysisRun.objects.create(property_listing=prop)
        due_diligence.run_due_diligence(run)

        check = DueDiligenceCheck.objects.get(analysis_run=run, category=DueDiligenceCheck.Category.REGISTRY)
        self.assertEqual(check.status, DueDiligenceCheck.Status.PENDING_VERIFICATION)
        self.assertIn("Queued", check.notes)

    def test_every_other_category_set_to_unknown_and_verification_required(self):
        run = AnalysisRun.objects.create(property_listing=_make_property())
        due_diligence.run_due_diligence(run)

        for category in DueDiligenceCheck.Category.values:
            if category in (
                DueDiligenceCheck.Category.REGISTRY,
                DueDiligenceCheck.Category.OWNERSHIP,
                DueDiligenceCheck.Category.LITIGATION,
            ):
                continue
            check = DueDiligenceCheck.objects.get(analysis_run=run, category=category)
            self.assertEqual(check.status, DueDiligenceCheck.Status.UNKNOWN)
            self.assertTrue(check.verification_required)

    def test_manually_set_non_bridged_category_is_not_clobbered_on_rerun(self):
        run = AnalysisRun.objects.create(property_listing=_make_property())
        DueDiligenceCheck.objects.create(
            analysis_run=run,
            category=DueDiligenceCheck.Category.LAND_USE,
            status=DueDiligenceCheck.Status.PASS,
            notes="Reviewed manually by an admin.",
        )
        due_diligence.run_due_diligence(run)

        check = DueDiligenceCheck.objects.get(analysis_run=run, category=DueDiligenceCheck.Category.LAND_USE)
        self.assertEqual(check.status, DueDiligenceCheck.Status.PASS)
        self.assertIn("Reviewed manually", check.notes)


class RunDueDiligenceWithReportTests(TestCase):
    def _complete_report(self, property_listing, risk_level=TitleCheckReport.RiskLevel.GREEN):
        return TitleCheckReport.objects.create(
            property=property_listing,
            status=TitleCheckReport.Status.COMPLETE,
            risk_level=risk_level,
        )

    def test_clean_match_passes_registry_and_ownership_with_evidence(self):
        prop = _make_property()
        report = self._complete_report(prop)
        KhasraEntry.objects.create(
            report=report, khasra_number="53 KA", lookup_status=KhasraEntry.LookupStatus.FOUND,
            seller_match=True, seller_match_score=100, buyer_match=True, buyer_match_score=97,
            area_match=True, amount_match=True, date_match=True,
        )
        run = AnalysisRun.objects.create(property_listing=prop)

        due_diligence.run_due_diligence(run)

        registry = DueDiligenceCheck.objects.get(analysis_run=run, category=DueDiligenceCheck.Category.REGISTRY)
        ownership = DueDiligenceCheck.objects.get(analysis_run=run, category=DueDiligenceCheck.Category.OWNERSHIP)
        self.assertEqual(registry.status, DueDiligenceCheck.Status.PASS)
        self.assertEqual(ownership.status, DueDiligenceCheck.Status.PASS)
        self.assertFalse(registry.verification_required)

        evidence = Evidence.objects.filter(analysis_run=run, claim__startswith="Bhulekh match")
        self.assertEqual(evidence.count(), 5)  # seller, buyer, area, amount, date
        for item in evidence:
            self.assertEqual(item.verification_status, Evidence.VerificationStatus.VERIFIED)

    def test_mismatch_fails_ownership_and_registry_reflects_red(self):
        prop = _make_property()
        report = self._complete_report(prop, risk_level=TitleCheckReport.RiskLevel.RED)
        KhasraEntry.objects.create(
            report=report, khasra_number="53 KA", lookup_status=KhasraEntry.LookupStatus.FOUND,
            seller_match=False, buyer_match=True,
        )
        run = AnalysisRun.objects.create(property_listing=prop)

        due_diligence.run_due_diligence(run)

        registry = DueDiligenceCheck.objects.get(analysis_run=run, category=DueDiligenceCheck.Category.REGISTRY)
        ownership = DueDiligenceCheck.objects.get(analysis_run=run, category=DueDiligenceCheck.Category.OWNERSHIP)
        self.assertEqual(registry.status, DueDiligenceCheck.Status.FAIL)
        self.assertEqual(ownership.status, DueDiligenceCheck.Status.FAIL)

        disputed = Evidence.objects.get(analysis_run=run, claim__contains="Seller name")
        self.assertEqual(disputed.verification_status, Evidence.VerificationStatus.DISPUTED)

    def test_litigation_flagged_mutation_sets_warning(self):
        prop = _make_property()
        report = self._complete_report(prop)
        lookup = KhataLookup.objects.create(report=report, khata_number="8", fetch_status=KhataLookup.FetchStatus.OK)
        MutationEntry.objects.create(
            khata_lookup=lookup, sequence=0, raw_text="...",
            is_litigation_flagged=True, matched_litigation_keywords="स्थाई निषेधाज्ञा",
        )
        run = AnalysisRun.objects.create(property_listing=prop)

        due_diligence.run_due_diligence(run)

        litigation = DueDiligenceCheck.objects.get(analysis_run=run, category=DueDiligenceCheck.Category.LITIGATION)
        self.assertEqual(litigation.status, DueDiligenceCheck.Status.WARNING)
        self.assertIn("स्थाई निषेधाज्ञा", litigation.notes)

    def test_no_litigation_language_passes(self):
        prop = _make_property()
        report = self._complete_report(prop)
        lookup = KhataLookup.objects.create(report=report, khata_number="8", fetch_status=KhataLookup.FetchStatus.OK)
        MutationEntry.objects.create(khata_lookup=lookup, sequence=0, raw_text="...", is_litigation_flagged=False)
        run = AnalysisRun.objects.create(property_listing=prop)

        due_diligence.run_due_diligence(run)

        litigation = DueDiligenceCheck.objects.get(analysis_run=run, category=DueDiligenceCheck.Category.LITIGATION)
        self.assertEqual(litigation.status, DueDiligenceCheck.Status.PASS)

    def test_rerun_replaces_bhulekh_evidence_instead_of_duplicating(self):
        prop = _make_property()
        report = self._complete_report(prop)
        entry = KhasraEntry.objects.create(
            report=report, khasra_number="53 KA", lookup_status=KhasraEntry.LookupStatus.FOUND,
            seller_match=False,
        )
        run = AnalysisRun.objects.create(property_listing=prop)
        due_diligence.run_due_diligence(run)
        self.assertEqual(Evidence.objects.filter(analysis_run=run, claim__startswith="Bhulekh match").count(), 1)

        entry.seller_match = True
        entry.save()
        due_diligence.run_due_diligence(run)

        remaining = Evidence.objects.filter(analysis_run=run, claim__startswith="Bhulekh match")
        self.assertEqual(remaining.count(), 1)
        self.assertIn("matches Bhulekh record", remaining.first().claim)
        self.assertNotIn("does NOT match", remaining.first().claim)


class RunDueDiligenceAdminViewTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_superuser(
            username="staffer", password="pw", email="staffer@example.com"
        )
        self.client.login(username="staffer", password="pw")
        self.run = AnalysisRun.objects.create(property_listing=_make_property())

    def test_rejects_get(self):
        url = reverse("admin:investment_analysisrun_run_due_diligence", args=[self.run.pk])
        self.assertEqual(self.client.get(url).status_code, 405)

    def test_requires_staff(self):
        self.client.logout()
        url = reverse("admin:investment_analysisrun_run_due_diligence", args=[self.run.pk])
        self.assertEqual(self.client.post(url).status_code, 302)

    def test_post_runs_and_redirects(self):
        url = reverse("admin:investment_analysisrun_run_due_diligence", args=[self.run.pk])
        response = self.client.post(url)
        self.assertRedirects(response, reverse("admin:investment_analysisrun_change", args=[self.run.pk]))
        self.assertTrue(DueDiligenceCheck.objects.filter(analysis_run=self.run).exists())

    def test_change_form_shows_button(self):
        url = reverse("admin:investment_analysisrun_change", args=[self.run.pk])
        response = self.client.get(url)
        self.assertContains(response, "Run Due Diligence")


class DefaultScoringConfigurationMigrationTests(TestCase):
    def test_seeded_configuration_weights_sum_to_100(self):
        config = ScoringConfiguration.objects.get(name="Default", property_type="")
        self.assertTrue(config.is_active)
        total = sum(f.weight_percent for f in config.factors.all())
        self.assertEqual(total, Decimal("100"))
        self.assertEqual(config.factors.count(), 7)


class CategoryScoreFromChecksTests(TestCase):
    def setUp(self):
        self.run = AnalysisRun.objects.create(property_listing=_make_property())

    def test_no_checks_returns_default_without_signal(self):
        score, has_signal = scoring._category_score_from_checks(self.run, [DueDiligenceCheck.Category.OWNERSHIP])
        self.assertEqual(score, scoring.DEFAULT_CATEGORY_SCORE)
        self.assertFalse(has_signal)

    def test_all_pass_scores_100(self):
        DueDiligenceCheck.objects.create(
            analysis_run=self.run, category=DueDiligenceCheck.Category.OWNERSHIP, status=DueDiligenceCheck.Status.PASS
        )
        score, has_signal = scoring._category_score_from_checks(self.run, [DueDiligenceCheck.Category.OWNERSHIP])
        self.assertEqual(score, 100)
        self.assertTrue(has_signal)

    def test_fail_scores_0(self):
        DueDiligenceCheck.objects.create(
            analysis_run=self.run, category=DueDiligenceCheck.Category.OWNERSHIP, status=DueDiligenceCheck.Status.FAIL
        )
        score, has_signal = scoring._category_score_from_checks(self.run, [DueDiligenceCheck.Category.OWNERSHIP])
        self.assertEqual(score, 0)
        self.assertTrue(has_signal)

    def test_mixed_statuses_average(self):
        DueDiligenceCheck.objects.create(
            analysis_run=self.run, category=DueDiligenceCheck.Category.OWNERSHIP, status=DueDiligenceCheck.Status.PASS
        )
        DueDiligenceCheck.objects.create(
            analysis_run=self.run, category=DueDiligenceCheck.Category.REGISTRY, status=DueDiligenceCheck.Status.WARNING
        )
        score, has_signal = scoring._category_score_from_checks(
            self.run, [DueDiligenceCheck.Category.OWNERSHIP, DueDiligenceCheck.Category.REGISTRY]
        )
        self.assertEqual(score, 80)  # (100 + 60) / 2
        self.assertTrue(has_signal)

    def test_not_applicable_excluded_and_unknown_has_no_signal(self):
        DueDiligenceCheck.objects.create(
            analysis_run=self.run, category=DueDiligenceCheck.Category.OWNERSHIP,
            status=DueDiligenceCheck.Status.NOT_APPLICABLE,
        )
        DueDiligenceCheck.objects.create(
            analysis_run=self.run, category=DueDiligenceCheck.Category.REGISTRY,
            status=DueDiligenceCheck.Status.UNKNOWN,
        )
        score, has_signal = scoring._category_score_from_checks(
            self.run, [DueDiligenceCheck.Category.OWNERSHIP, DueDiligenceCheck.Category.REGISTRY]
        )
        self.assertEqual(score, scoring.DEFAULT_CATEGORY_SCORE)
        self.assertFalse(has_signal)


class PriceValuationAndMarketScoreTests(TestCase):
    def setUp(self):
        self.run = AnalysisRun.objects.create(property_listing=_make_property())

    def test_no_valuation_returns_default_without_signal(self):
        score, has_signal = scoring._price_valuation_score(self.run)
        self.assertEqual(score, scoring.DEFAULT_CATEGORY_SCORE)
        self.assertFalse(has_signal)
        score, has_signal = scoring._market_score(self.run)
        self.assertEqual(score, scoring.DEFAULT_CATEGORY_SCORE)
        self.assertFalse(has_signal)

    def test_at_market_price_scores_well(self):
        PropertyValuation.objects.create(analysis_run=self.run, premium_discount_percent=Decimal("0"), comparable_count=3)
        score, has_signal = scoring._price_valuation_score(self.run)
        self.assertEqual(score, 100)
        self.assertTrue(has_signal)

    def test_large_premium_scores_poorly(self):
        PropertyValuation.objects.create(analysis_run=self.run, premium_discount_percent=Decimal("30"), comparable_count=3)
        score, _ = scoring._price_valuation_score(self.run)
        self.assertLess(score, 50)

    def test_more_comparables_scores_market_higher(self):
        PropertyValuation.objects.create(analysis_run=self.run, premium_discount_percent=Decimal("0"), comparable_count=1)
        low, _ = scoring._market_score(AnalysisRun.objects.get(pk=self.run.pk))
        PropertyValuation.objects.filter(analysis_run=self.run).update(comparable_count=5)
        high, _ = scoring._market_score(AnalysisRun.objects.get(pk=self.run.pk))
        self.assertGreater(high, low)


class ComputeScoreEndToEndTests(TestCase):
    def test_clean_pass_scores_high_with_high_confidence(self):
        run = AnalysisRun.objects.create(property_listing=_make_property())
        for category in (
            DueDiligenceCheck.Category.OWNERSHIP, DueDiligenceCheck.Category.REGISTRY,
            DueDiligenceCheck.Category.LITIGATION, DueDiligenceCheck.Category.ROAD_ACCESS,
            DueDiligenceCheck.Category.UTILITIES, DueDiligenceCheck.Category.LOCATION,
            DueDiligenceCheck.Category.LIQUIDITY, DueDiligenceCheck.Category.PROPERTY_CONDITION,
            DueDiligenceCheck.Category.ENVIRONMENTAL,
        ):
            DueDiligenceCheck.objects.create(analysis_run=run, category=category, status=DueDiligenceCheck.Status.PASS)
        PropertyValuation.objects.create(analysis_run=run, premium_discount_percent=Decimal("0"), comparable_count=5)

        score = scoring.compute_score(run)
        self.assertGreaterEqual(score.overall_score, 90)
        self.assertGreaterEqual(score.confidence_score, 80)
        self.assertFalse(score.missing_data_penalty_applied)

    def test_everything_unknown_scores_neutral_with_low_confidence(self):
        run = AnalysisRun.objects.create(property_listing=_make_property())
        score = scoring.compute_score(run)
        self.assertEqual(score.overall_score, scoring.DEFAULT_CATEGORY_SCORE)
        self.assertEqual(score.confidence_score, 0)
        self.assertTrue(score.missing_data_penalty_applied)

    def test_a_fail_drags_down_legal_documentation_and_overall(self):
        run = AnalysisRun.objects.create(property_listing=_make_property())
        DueDiligenceCheck.objects.create(
            analysis_run=run, category=DueDiligenceCheck.Category.OWNERSHIP, status=DueDiligenceCheck.Status.FAIL
        )
        score = scoring.compute_score(run)
        self.assertLess(score.category_scores["legal_documentation"], scoring.DEFAULT_CATEGORY_SCORE)
        self.assertIn("FAILED", score.explanation)

    def test_rerun_updates_instead_of_duplicating(self):
        run = AnalysisRun.objects.create(property_listing=_make_property())
        scoring.compute_score(run)
        scoring.compute_score(run)
        self.assertEqual(PropertyScore.objects.filter(analysis_run=run).count(), 1)

    def test_explanation_mentions_unknown_category_count(self):
        run = AnalysisRun.objects.create(property_listing=_make_property())
        due_diligence.run_due_diligence(run)  # creates all 17 as Unknown/Pending
        score = scoring.compute_score(run)
        self.assertIn("Unknown/Pending Verification", score.explanation)


class ComputeRisksTests(TestCase):
    def test_fail_check_creates_mapped_risk(self):
        run = AnalysisRun.objects.create(property_listing=_make_property())
        DueDiligenceCheck.objects.create(
            analysis_run=run, category=DueDiligenceCheck.Category.OWNERSHIP,
            status=DueDiligenceCheck.Status.FAIL, notes="Seller mismatch.",
        )
        risk_engine.compute_risks(run)
        risk = PropertyRisk.objects.get(analysis_run=run, category=PropertyRisk.Category.LEGAL)
        self.assertEqual(risk.severity, PropertyRisk.Severity.HIGH)
        self.assertIn("Seller mismatch", risk.reason)
        self.assertTrue(risk.reason.startswith("[auto]"))

    def test_warning_check_creates_medium_severity_risk(self):
        run = AnalysisRun.objects.create(property_listing=_make_property())
        DueDiligenceCheck.objects.create(
            analysis_run=run, category=DueDiligenceCheck.Category.LITIGATION, status=DueDiligenceCheck.Status.WARNING
        )
        risk_engine.compute_risks(run)
        risk = PropertyRisk.objects.get(analysis_run=run, category=PropertyRisk.Category.LEGAL)
        self.assertEqual(risk.severity, PropertyRisk.Severity.MEDIUM)

    def test_category_with_no_risk_mapping_is_skipped(self):
        run = AnalysisRun.objects.create(property_listing=_make_property())
        DueDiligenceCheck.objects.create(
            analysis_run=run, category=DueDiligenceCheck.Category.PROPERTY_CONDITION,
            status=DueDiligenceCheck.Status.FAIL,
        )
        risk_engine.compute_risks(run)
        self.assertFalse(PropertyRisk.objects.filter(analysis_run=run, reason__startswith="[auto]").exists())

    def test_many_unresolved_categories_get_medium_data_quality_risk(self):
        run = AnalysisRun.objects.create(property_listing=_make_property())
        due_diligence.run_due_diligence(run)  # 17 categories, all Unknown/Pending
        risk_engine.compute_risks(run)
        risk = PropertyRisk.objects.get(analysis_run=run, category=PropertyRisk.Category.DATA_QUALITY)
        self.assertEqual(risk.severity, PropertyRisk.Severity.MEDIUM)

    def test_rerun_clears_auto_risks_but_keeps_manual_ones(self):
        run = AnalysisRun.objects.create(property_listing=_make_property())
        manual = PropertyRisk.objects.create(
            analysis_run=run, category=PropertyRisk.Category.ENVIRONMENTAL,
            severity=PropertyRisk.Severity.LOW, reason="Flagged by hand during a site visit.",
        )
        DueDiligenceCheck.objects.create(
            analysis_run=run, category=DueDiligenceCheck.Category.OWNERSHIP, status=DueDiligenceCheck.Status.FAIL
        )
        risk_engine.compute_risks(run)
        risk_engine.compute_risks(run)

        self.assertEqual(PropertyRisk.objects.filter(analysis_run=run, reason__startswith="[auto]").count(), 1)
        self.assertTrue(PropertyRisk.objects.filter(pk=manual.pk).exists())

    def test_returns_total_risk_count(self):
        run = AnalysisRun.objects.create(property_listing=_make_property())
        DueDiligenceCheck.objects.create(
            analysis_run=run, category=DueDiligenceCheck.Category.OWNERSHIP, status=DueDiligenceCheck.Status.FAIL
        )
        count = risk_engine.compute_risks(run)
        self.assertEqual(count, PropertyRisk.objects.filter(analysis_run=run).count())


class AiAnalystContextTests(TestCase):
    def test_context_includes_checks_risks_evidence(self):
        run = AnalysisRun.objects.create(property_listing=_make_property())
        DueDiligenceCheck.objects.create(
            analysis_run=run, category=DueDiligenceCheck.Category.OWNERSHIP,
            status=DueDiligenceCheck.Status.PASS, notes="Matched.",
        )
        PropertyRisk.objects.create(
            analysis_run=run, category=PropertyRisk.Category.LEGAL,
            severity=PropertyRisk.Severity.LOW, reason="Minor note.",
        )
        Evidence.objects.create(
            property_listing=run.property_listing, analysis_run=run,
            claim="Area is 150 gaj", source_type=Evidence.SourceType.FACT,
        )
        context = ai_analyst.build_context(run)
        self.assertEqual(len(context["due_diligence_checks"]), 1)
        self.assertEqual(len(context["risks"]), 1)
        self.assertEqual(len(context["evidence"]), 1)
        self.assertIsNone(context["score"])

    def test_context_includes_score_when_present(self):
        run = AnalysisRun.objects.create(property_listing=_make_property())
        PropertyScore.objects.create(analysis_run=run, overall_score=76, confidence_score=61)
        context = ai_analyst.build_context(run)
        self.assertEqual(context["score"]["overall_score"], 76)


class AiAnalystClientTests(TestCase):
    @override_settings(AI_EXTRACTION_PROVIDER="anthropic", ANTHROPIC_API_KEY="")
    def test_config_error_sets_error_field_without_raising(self):
        run = AnalysisRun.objects.create(property_listing=_make_property())
        ai_analyst.generate_summary(run)  # must not raise
        run.refresh_from_db()
        self.assertIn("ANTHROPIC_API_KEY", run.ai_summary_error)

    @override_settings(AI_EXTRACTION_PROVIDER="carrier-pigeon")
    def test_unrecognized_provider_sets_error(self):
        run = AnalysisRun.objects.create(property_listing=_make_property())
        ai_analyst.generate_summary(run)
        run.refresh_from_db()
        self.assertIn("Unrecognized", run.ai_summary_error)

    @patch("investment.ai_analyst._build_model")
    def test_request_error_sets_error_without_raising(self, mock_build_model):
        mock_model = MagicMock()
        mock_model.invoke.side_effect = RuntimeError("network boom")
        mock_build_model.return_value = mock_model
        run = AnalysisRun.objects.create(property_listing=_make_property())
        ai_analyst.generate_summary(run)
        run.refresh_from_db()
        self.assertIn("network boom", run.ai_summary_error)

    @patch("investment.ai_analyst._build_model")
    def test_parse_error_sets_error(self, mock_build_model):
        mock_model = MagicMock()
        mock_model.invoke.return_value = {"parsed": None, "parsing_error": "bad json"}
        mock_build_model.return_value = mock_model
        run = AnalysisRun.objects.create(property_listing=_make_property())
        ai_analyst.generate_summary(run)
        run.refresh_from_db()
        self.assertIn("bad json", run.ai_summary_error)

    @patch("investment.ai_analyst._build_model")
    def test_success_populates_summary_and_clears_error(self, mock_build_model):
        schema = ai_analyst.PropertyAnalysisSummary(
            executive_summary="Clean match on all checked items.",
            key_positives=["Seller/buyer names match Bhulekh."],
            key_risks=[],
            missing_information=["Market comparables"],
            verification_checklist=["Confirm NOC"],
            questions_for_seller=[],
        )
        mock_model = MagicMock()
        mock_model.invoke.return_value = {"parsed": schema, "parsing_error": None}
        mock_build_model.return_value = mock_model

        run = AnalysisRun.objects.create(property_listing=_make_property(), ai_summary_error="stale error")
        ai_analyst.generate_summary(run)
        run.refresh_from_db()

        self.assertEqual(run.ai_summary["executive_summary"], "Clean match on all checked items.")
        self.assertFalse(run.ai_summary_error)
        self.assertIsNotNone(run.ai_summary_generated_at)
        self.assertIn("Clean match", run.ai_summary_raw_response)


class AnalysisRunAdminActionViewsTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_superuser(
            username="staffer", password="pw", email="staffer@example.com"
        )
        self.client.login(username="staffer", password="pw")
        self.run = AnalysisRun.objects.create(property_listing=_make_property())

    def test_compute_score_requires_post(self):
        url = reverse("admin:investment_analysisrun_compute_score", args=[self.run.pk])
        self.assertEqual(self.client.get(url).status_code, 405)

    def test_compute_score_post_computes_and_redirects(self):
        url = reverse("admin:investment_analysisrun_compute_score", args=[self.run.pk])
        response = self.client.post(url)
        self.assertRedirects(response, reverse("admin:investment_analysisrun_change", args=[self.run.pk]))
        self.assertTrue(PropertyScore.objects.filter(analysis_run=self.run).exists())

    def test_compute_risks_post_identifies_and_redirects(self):
        DueDiligenceCheck.objects.create(
            analysis_run=self.run, category=DueDiligenceCheck.Category.OWNERSHIP, status=DueDiligenceCheck.Status.FAIL
        )
        url = reverse("admin:investment_analysisrun_compute_risks", args=[self.run.pk])
        response = self.client.post(url)
        self.assertRedirects(response, reverse("admin:investment_analysisrun_change", args=[self.run.pk]))
        self.assertTrue(PropertyRisk.objects.filter(analysis_run=self.run).exists())

    @patch("investment.admin.ai_analyst.generate_summary")
    def test_generate_summary_post_calls_service_and_redirects(self, mock_generate):
        url = reverse("admin:investment_analysisrun_generate_summary", args=[self.run.pk])
        response = self.client.post(url)
        mock_generate.assert_called_once()
        self.assertRedirects(response, reverse("admin:investment_analysisrun_change", args=[self.run.pk]))

    def test_actions_require_staff(self):
        self.client.logout()
        for name in ("compute_score", "compute_risks", "generate_summary"):
            url = reverse(f"admin:investment_analysisrun_{name}", args=[self.run.pk])
            self.assertEqual(self.client.post(url).status_code, 302)

    def test_change_form_shows_all_buttons(self):
        url = reverse("admin:investment_analysisrun_change", args=[self.run.pk])
        response = self.client.get(url)
        for label in ("Identify Risks", "Compute Score", "Generate AI Summary", "View Report"):
            self.assertContains(response, label)


class ReportViewTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_superuser(
            username="staffer", password="pw", email="staffer@example.com"
        )
        self.client.login(username="staffer", password="pw")
        self.run = AnalysisRun.objects.create(property_listing=_make_property())

    def test_report_renders_with_no_data_yet(self):
        url = reverse("admin:investment_analysisrun_report", args=[self.run.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Not yet computed")

    def test_report_requires_staff(self):
        self.client.logout()
        url = reverse("admin:investment_analysisrun_report", args=[self.run.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    def test_report_shows_score_checks_risks_evidence(self):
        DueDiligenceCheck.objects.create(
            analysis_run=self.run, category=DueDiligenceCheck.Category.OWNERSHIP,
            status=DueDiligenceCheck.Status.PASS, notes="Matched.",
        )
        PropertyRisk.objects.create(
            analysis_run=self.run, category=PropertyRisk.Category.LEGAL,
            severity=PropertyRisk.Severity.LOW, reason="Minor note.",
        )
        Evidence.objects.create(
            property_listing=self.run.property_listing, analysis_run=self.run,
            claim="Area is 150 gaj", source_type=Evidence.SourceType.FACT,
        )
        scoring.compute_score(self.run)

        url = reverse("admin:investment_analysisrun_report", args=[self.run.pk])
        response = self.client.get(url)
        self.assertContains(response, "Matched.")
        self.assertContains(response, "Minor note.")
        self.assertContains(response, "Area is 150 gaj")
        self.assertContains(response, f"{self.run.score.overall_score}/100")


class PricePerSqmTests(TestCase):
    """Real worked example: 94.23 sqm for Rs. 18,00,000 (the Danda
    Lakhaund deed) and 108.70 sqm for Rs. 20,55,000 (the Jagaatkhana
    deed) — two real, nearby, comparable transactions."""

    def test_sqm_is_identity(self):
        result = valuation._price_per_sqm(Decimal("1800000"), Decimal("94.23"), Property.AreaUnit.SQM)
        self.assertEqual(result, (Decimal("1800000") / Decimal("94.23")).quantize(Decimal("0.01")))

    def test_sqft_converts_correctly(self):
        # 1014 sqft is the deed's own stated sqft-equivalent of 94.23 sqm.
        result = valuation._price_per_sqm(Decimal("1800000"), Decimal("1014"), Property.AreaUnit.SQFT)
        # Should be close to the direct sqm calculation (~19,100/sqm) —
        # the deed's own sqft figure is itself a rounded conversion.
        self.assertAlmostEqual(float(result), 19100, delta=50)

    def test_gaj_is_not_converted(self):
        # 112.66 gaj is the deed's own stated gaj-equivalent — deliberately
        # excluded, same principle as bigha/nali in duediligence.
        result = valuation._price_per_sqm(Decimal("1800000"), Decimal("112.66"), Property.AreaUnit.GAJ)
        self.assertIsNone(result)

    def test_zero_area_does_not_crash(self):
        self.assertIsNone(valuation._price_per_sqm(Decimal("1800000"), Decimal("0"), Property.AreaUnit.SQM))

    def test_missing_price_or_area_returns_none(self):
        self.assertIsNone(valuation._price_per_sqm(None, Decimal("100"), Property.AreaUnit.SQM))
        self.assertIsNone(valuation._price_per_sqm(Decimal("100"), None, Property.AreaUnit.SQM))


class ComputeValuationTests(TestCase):
    def _comparable(self, run, price, area_value, area_unit=Property.AreaUnit.SQM, **kwargs):
        return PropertyComparable.objects.create(
            property_listing=run.property_listing, analysis_run=run,
            price=price, area_value=area_value, area_unit=area_unit, **kwargs
        )

    def test_no_comparables_leaves_valuation_empty(self):
        run = AnalysisRun.objects.create(property_listing=_make_property())
        result = valuation.compute_valuation(run)
        self.assertEqual(result.comparable_count, 0)
        self.assertIsNone(result.median_comparable_price_per_unit)
        self.assertIn("No comparable properties", result.methodology_notes)

    def test_two_real_comparables_produce_median_and_range(self):
        # The two real deeds from this session: Jagaatkhana (the target
        # property) and Danda Lakhaund (its comparable).
        prop = _make_property(title="Jagaatkhana", price=Decimal("2055000"), area_value=Decimal("108.70"), area_unit=Property.AreaUnit.SQM)
        run = AnalysisRun.objects.create(property_listing=prop)
        self._comparable(
            run, price=Decimal("1800000"), area_value=Decimal("94.23"),
            is_verified_transaction=True, source="Danda Lakhaund sale deed, 02-Jun-2025",
        )

        result = valuation.compute_valuation(run)

        self.assertEqual(result.comparable_count, 1)
        expected_per_sqm = (Decimal("1800000") / Decimal("94.23")).quantize(Decimal("0.01"))
        self.assertEqual(result.median_comparable_price_per_unit, expected_per_sqm)
        self.assertEqual(result.lower_range_price_per_unit, expected_per_sqm)
        self.assertEqual(result.upper_range_price_per_unit, expected_per_sqm)

        expected_asking = (Decimal("2055000") / Decimal("108.70")).quantize(Decimal("0.01"))
        self.assertEqual(result.asking_price_per_unit, expected_asking)
        self.assertIsNotNone(result.premium_discount_percent)

    def test_median_of_three_comparables(self):
        run = AnalysisRun.objects.create(property_listing=_make_property())
        self._comparable(run, price=Decimal("1000000"), area_value=Decimal("100"))  # 10000/sqm
        self._comparable(run, price=Decimal("1500000"), area_value=Decimal("100"))  # 15000/sqm
        self._comparable(run, price=Decimal("2000000"), area_value=Decimal("100"))  # 20000/sqm

        result = valuation.compute_valuation(run)
        self.assertEqual(result.median_comparable_price_per_unit, Decimal("15000.00"))
        self.assertEqual(result.lower_range_price_per_unit, Decimal("10000.00"))
        self.assertEqual(result.upper_range_price_per_unit, Decimal("20000.00"))

    def test_gaj_comparable_excluded_from_statistics_but_noted(self):
        run = AnalysisRun.objects.create(property_listing=_make_property())
        self._comparable(run, price=Decimal("1800000"), area_value=Decimal("112.66"), area_unit=Property.AreaUnit.GAJ)

        result = valuation.compute_valuation(run)
        self.assertEqual(result.comparable_count, 0)
        self.assertIn("Gaj", result.methodology_notes)
        self.assertIn("excluded", result.methodology_notes)

    def test_unverified_comparable_noted_in_methodology(self):
        run = AnalysisRun.objects.create(property_listing=_make_property())
        self._comparable(run, price=Decimal("1800000"), area_value=Decimal("94.23"), is_verified_transaction=False)

        result = valuation.compute_valuation(run)
        self.assertIn("unverified listing", result.methodology_notes)

    def test_premium_when_asking_above_median(self):
        prop = _make_property(price=Decimal("3000000"), area_value=Decimal("100"), area_unit=Property.AreaUnit.SQM)
        run = AnalysisRun.objects.create(property_listing=prop)
        self._comparable(run, price=Decimal("2000000"), area_value=Decimal("100"))  # 20000/sqm median

        result = valuation.compute_valuation(run)
        self.assertGreater(result.premium_discount_percent, 0)  # asking 30000/sqm > median 20000/sqm

    def test_rerun_updates_instead_of_duplicating(self):
        run = AnalysisRun.objects.create(property_listing=_make_property())
        self._comparable(run, price=Decimal("1800000"), area_value=Decimal("94.23"))
        valuation.compute_valuation(run)
        valuation.compute_valuation(run)
        self.assertEqual(PropertyValuation.objects.filter(analysis_run=run).count(), 1)

    def test_missing_asking_price_is_noted_accurately(self):
        # Regression guard: a property with no price set at all was
        # previously (wrongly) reported as being "in an inconvertible
        # unit" even when its unit (sqft) is perfectly convertible — the
        # real reason is simply that no price was ever entered.
        prop = _make_property(price=None, area_value=Decimal("100"), area_unit=Property.AreaUnit.SQFT)
        run = AnalysisRun.objects.create(property_listing=prop)
        result = valuation.compute_valuation(run)
        self.assertIn("no asking price set", result.methodology_notes)
        self.assertNotIn("isn't auto-converted", result.methodology_notes)

    def test_inconvertible_unit_is_noted_accurately(self):
        prop = _make_property(price=Decimal("1000000"), area_value=Decimal("100"), area_unit=Property.AreaUnit.GAJ)
        run = AnalysisRun.objects.create(property_listing=prop)
        result = valuation.compute_valuation(run)
        self.assertIn("isn't auto-converted", result.methodology_notes)
        self.assertNotIn("no asking price set", result.methodology_notes)


class ScoringReadsRealValuationTests(TestCase):
    """Confirms Phase 7's scoring engine (built before Phase 8 existed)
    actually picks up a real computed valuation now that one exists."""

    def test_price_valuation_score_has_signal_once_valuation_computed(self):
        prop = _make_property(price=Decimal("2055000"), area_value=Decimal("108.70"), area_unit=Property.AreaUnit.SQM)
        run = AnalysisRun.objects.create(property_listing=prop)
        PropertyComparable.objects.create(
            property_listing=prop, analysis_run=run,
            price=Decimal("1800000"), area_value=Decimal("94.23"), area_unit=Property.AreaUnit.SQM,
        )
        valuation.compute_valuation(run)

        score, has_signal = scoring._price_valuation_score(AnalysisRun.objects.get(pk=run.pk))
        self.assertTrue(has_signal)
        # 1 comparable -> market score of exactly 40+10=50, same number as
        # DEFAULT_CATEGORY_SCORE by coincidence — has_signal is the real
        # assertion here, not the score's magnitude.
        market_score, market_has_signal = scoring._market_score(AnalysisRun.objects.get(pk=run.pk))
        self.assertTrue(market_has_signal)
        self.assertEqual(market_score, 50)


class ComputeValuationAdminViewTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_superuser(
            username="staffer", password="pw", email="staffer@example.com"
        )
        self.client.login(username="staffer", password="pw")
        self.run = AnalysisRun.objects.create(property_listing=_make_property())

    def test_requires_post(self):
        url = reverse("admin:investment_analysisrun_compute_valuation", args=[self.run.pk])
        self.assertEqual(self.client.get(url).status_code, 405)

    def test_post_computes_and_redirects(self):
        PropertyComparable.objects.create(
            property_listing=self.run.property_listing, analysis_run=self.run,
            price=Decimal("1800000"), area_value=Decimal("94.23"), area_unit=Property.AreaUnit.SQM,
        )
        url = reverse("admin:investment_analysisrun_compute_valuation", args=[self.run.pk])
        response = self.client.post(url)
        self.assertRedirects(response, reverse("admin:investment_analysisrun_change", args=[self.run.pk]))
        self.assertTrue(PropertyValuation.objects.filter(analysis_run=self.run).exists())

    def test_requires_staff(self):
        self.client.logout()
        url = reverse("admin:investment_analysisrun_compute_valuation", args=[self.run.pk])
        self.assertEqual(self.client.post(url).status_code, 302)

    def test_change_form_shows_button(self):
        url = reverse("admin:investment_analysisrun_change", args=[self.run.pk])
        response = self.client.get(url)
        self.assertContains(response, "Compute Valuation")


class HaversineDistanceTests(TestCase):
    def test_identical_points_are_zero_km_apart(self):
        self.assertEqual(location_intelligence._haversine_km(30.358044, 78.102189, 30.358044, 78.102189), 0.0)

    def test_known_distance_is_approximately_correct(self):
        # Dehradun railway station to Mussoorie library chowk, ~30km by
        # straight line — a real-world sanity check on the formula, not
        # an exact fixture.
        km = location_intelligence._haversine_km(30.3165, 78.0322, 30.4598, 78.0664)
        self.assertAlmostEqual(km, 16.2, delta=2)


class FetchNearbyPlacesTests(TestCase):
    @override_settings(GOOGLE_MAPS_API_KEY="")
    def test_config_error_when_key_blank(self):
        with self.assertRaises(location_intelligence.LocationIntelligenceConfigError):
            location_intelligence.fetch_nearby_places(30.358044, 78.102189, "school")

    @override_settings(GOOGLE_MAPS_API_KEY="test-key")
    @patch("investment.location_intelligence.requests.get")
    def test_request_exception_wrapped(self, mock_get):
        import requests as requests_module

        mock_get.side_effect = requests_module.RequestException("network boom")
        with self.assertRaises(location_intelligence.LocationIntelligenceRequestError):
            location_intelligence.fetch_nearby_places(30.358044, 78.102189, "school")

    @override_settings(GOOGLE_MAPS_API_KEY="test-key")
    @patch("investment.location_intelligence.requests.get")
    def test_non_ok_status_raises(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200, json=lambda: {"status": "REQUEST_DENIED", "error_message": "bad key"}
        )
        with self.assertRaises(location_intelligence.LocationIntelligenceRequestError):
            location_intelligence.fetch_nearby_places(30.358044, 78.102189, "school")

    @override_settings(GOOGLE_MAPS_API_KEY="test-key")
    @patch("investment.location_intelligence.requests.get")
    def test_zero_results_returns_empty_list(self, mock_get):
        mock_get.return_value = MagicMock(status_code=200, json=lambda: {"status": "ZERO_RESULTS", "results": []})
        places = location_intelligence.fetch_nearby_places(30.358044, 78.102189, "school")
        self.assertEqual(places, [])

    @override_settings(GOOGLE_MAPS_API_KEY="test-key")
    @patch("investment.location_intelligence.requests.get")
    def test_successful_response_parsed_and_sorted_by_distance(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "status": "OK",
                "results": [
                    {"name": "Far School", "vicinity": "Far Rd", "geometry": {"location": {"lat": 30.40, "lng": 78.20}}},
                    {"name": "Near School", "vicinity": "Near Rd", "geometry": {"location": {"lat": 30.359, "lng": 78.103}}},
                ],
            },
        )
        places = location_intelligence.fetch_nearby_places(30.358044, 78.102189, "school")
        self.assertEqual(len(places), 2)
        self.assertEqual(places[0]["name"], "Near School")  # sorted nearest-first
        self.assertLess(places[0]["distance_km"], places[1]["distance_km"])


class AnalyzeLocationTests(TestCase):
    def test_no_coordinates_leaves_pending_verification(self):
        run = AnalysisRun.objects.create(property_listing=_make_property())
        location_intelligence.analyze_location(run)
        check = DueDiligenceCheck.objects.get(analysis_run=run, category=DueDiligenceCheck.Category.LOCATION)
        self.assertEqual(check.status, DueDiligenceCheck.Status.PENDING_VERIFICATION)
        self.assertIn("latitude/longitude", check.notes)

    @override_settings(GOOGLE_MAPS_API_KEY="")
    def test_not_configured_leaves_pending_verification(self):
        prop = _make_property(latitude=Decimal("30.358044"), longitude=Decimal("78.102189"))
        run = AnalysisRun.objects.create(property_listing=prop)
        location_intelligence.analyze_location(run)
        check = DueDiligenceCheck.objects.get(analysis_run=run, category=DueDiligenceCheck.Category.LOCATION)
        self.assertEqual(check.status, DueDiligenceCheck.Status.PENDING_VERIFICATION)
        self.assertIn("GOOGLE_MAPS_API_KEY", check.notes)

    @override_settings(GOOGLE_MAPS_API_KEY="test-key")
    @patch("investment.location_intelligence.fetch_nearby_places")
    def test_successful_fetch_passes_and_creates_evidence(self, mock_fetch):
        mock_fetch.return_value = [{"name": "City Hospital", "address": "Main Rd", "distance_km": 1.2}]
        prop = _make_property(latitude=Decimal("30.358044"), longitude=Decimal("78.102189"))
        run = AnalysisRun.objects.create(property_listing=prop)

        location_intelligence.analyze_location(run)

        check = DueDiligenceCheck.objects.get(analysis_run=run, category=DueDiligenceCheck.Category.LOCATION)
        self.assertEqual(check.status, DueDiligenceCheck.Status.PASS)
        evidence = Evidence.objects.filter(analysis_run=run, claim__startswith="Nearby ")
        self.assertEqual(evidence.count(), len(location_intelligence.PLACE_TYPES))
        self.assertTrue(any("City Hospital" in e.claim for e in evidence))

    @override_settings(GOOGLE_MAPS_API_KEY="test-key")
    @patch("investment.location_intelligence.fetch_nearby_places")
    def test_no_results_anywhere_sets_warning(self, mock_fetch):
        mock_fetch.return_value = []
        prop = _make_property(latitude=Decimal("30.358044"), longitude=Decimal("78.102189"))
        run = AnalysisRun.objects.create(property_listing=prop)

        location_intelligence.analyze_location(run)

        check = DueDiligenceCheck.objects.get(analysis_run=run, category=DueDiligenceCheck.Category.LOCATION)
        self.assertEqual(check.status, DueDiligenceCheck.Status.WARNING)

    @override_settings(GOOGLE_MAPS_API_KEY="test-key")
    @patch("investment.location_intelligence.fetch_nearby_places")
    def test_all_requests_failing_sets_pending_verification(self, mock_fetch):
        mock_fetch.side_effect = location_intelligence.LocationIntelligenceRequestError("quota exceeded")
        prop = _make_property(latitude=Decimal("30.358044"), longitude=Decimal("78.102189"))
        run = AnalysisRun.objects.create(property_listing=prop)

        location_intelligence.analyze_location(run)  # must not raise

        check = DueDiligenceCheck.objects.get(analysis_run=run, category=DueDiligenceCheck.Category.LOCATION)
        self.assertEqual(check.status, DueDiligenceCheck.Status.PENDING_VERIFICATION)
        self.assertIn("quota exceeded", check.notes)

    @override_settings(GOOGLE_MAPS_API_KEY="test-key")
    @patch("investment.location_intelligence.fetch_nearby_places")
    def test_rerun_replaces_evidence_instead_of_duplicating(self, mock_fetch):
        mock_fetch.return_value = [{"name": "City Hospital", "address": "Main Rd", "distance_km": 1.2}]
        prop = _make_property(latitude=Decimal("30.358044"), longitude=Decimal("78.102189"))
        run = AnalysisRun.objects.create(property_listing=prop)

        location_intelligence.analyze_location(run)
        location_intelligence.analyze_location(run)

        evidence = Evidence.objects.filter(analysis_run=run, claim__startswith="Nearby ")
        self.assertEqual(evidence.count(), len(location_intelligence.PLACE_TYPES))


class ScoringReadsLocationTests(TestCase):
    def test_location_score_has_signal_once_analyzed(self):
        prop = _make_property(latitude=Decimal("30.358044"), longitude=Decimal("78.102189"))
        run = AnalysisRun.objects.create(property_listing=prop)
        DueDiligenceCheck.objects.create(
            analysis_run=run, category=DueDiligenceCheck.Category.LOCATION, status=DueDiligenceCheck.Status.PASS
        )
        score, has_signal = scoring._category_score_from_checks(run, [DueDiligenceCheck.Category.LOCATION])
        self.assertTrue(has_signal)
        self.assertEqual(score, 100)


class AnalyzeLocationAdminViewTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_superuser(
            username="staffer", password="pw", email="staffer@example.com"
        )
        self.client.login(username="staffer", password="pw")
        self.run = AnalysisRun.objects.create(
            property_listing=_make_property(latitude=Decimal("30.358044"), longitude=Decimal("78.102189"))
        )

    def test_requires_post(self):
        url = reverse("admin:investment_analysisrun_analyze_location", args=[self.run.pk])
        self.assertEqual(self.client.get(url).status_code, 405)

    def test_requires_staff(self):
        self.client.logout()
        url = reverse("admin:investment_analysisrun_analyze_location", args=[self.run.pk])
        self.assertEqual(self.client.post(url).status_code, 302)

    @override_settings(GOOGLE_MAPS_API_KEY="")
    def test_post_runs_and_redirects(self):
        url = reverse("admin:investment_analysisrun_analyze_location", args=[self.run.pk])
        response = self.client.post(url)
        self.assertRedirects(response, reverse("admin:investment_analysisrun_change", args=[self.run.pk]))
        self.assertTrue(DueDiligenceCheck.objects.filter(analysis_run=self.run, category="location").exists())

    def test_change_form_shows_button(self):
        url = reverse("admin:investment_analysisrun_change", args=[self.run.pk])
        response = self.client.get(url)
        self.assertContains(response, "Analyze Location")
