import shutil
import tempfile
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from . import bhulekh, deed_ai, parsing, risk, services
from .models import KhasraEntry, KhataLookup, MutationEntry, TitleCheckReport

# Real fetched fragment (Khata 222, Village डांडालखौण्ड, Dehradun, 2026-08-16)
# — see parsing.py's module docstring for the worked-example breakdown.
REAL_ENTRY_HTML = (
    '<td style="font-size: 12px;" class="order-text"><span>'
    "आ.अपर तह.सदर देहरादून वाद सं.7700/25/26.07.2025 खाता सं. 222 खसरा न. 410 "
    "रकबा 46 वर्गमीटर व खसरा न. 409 रकबा 48.23 वर्गमीटर कुल रकबा 0.0094है. ल. "
    "परता से विक्रेता पुरण सिंह राणा पुत्र पुश्य सिंह राणा नि. 74 कुमराडा, "
    "बल्डोगी तहसील चिन्याली सौड उत्तरकाशी का नाम खारिज होकर क्रेता राजीव "
    "मित्तल पुत्र नरेन्द्र कुमार मित्तल नि. 171/2 इन्दु वाटिका दक्षिण सिविल "
    "लाईन मुजफ्फरनगर, मुजफ्फरनगर सिटी उ.प्र. का नाम दर्ज होवे. बैनामा "
    "1800000/-02.06.2025</span></td>"
)


class ParsingTests(TestCase):
    def test_real_worked_example(self):
        entries = parsing.parse_mutation_entries(REAL_ENTRY_HTML)
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["case_number"], "7700/25")
        self.assertEqual(entry["order_date"], date(2025, 7, 26))
        self.assertEqual(entry["khata_number_in_entry"], "222")
        self.assertEqual(entry["khasra_numbers_in_entry"], "410,409")
        self.assertIn("410: 46", entry["area_text"])
        self.assertIn("409: 48.23", entry["area_text"])
        self.assertIn("पुरण सिंह राणा", entry["seller_name"])
        self.assertIn("राजीव मित्तल", entry["buyer_name"])
        self.assertNotIn("पुरण सिंह राणा", entry["buyer_name"])  # substring-match regression guard
        self.assertEqual(entry["deed_amount_text"], "1800000")
        self.assertEqual(entry["deed_date"], date(2025, 6, 2))
        self.assertFalse(entry["is_litigation_flagged"])

    def test_litigation_keyword_flagged_without_breaking_field_parsing(self):
        html = REAL_ENTRY_HTML.replace(
            "कुल रकबा 0.0094है.", "कुल रकबा 0.0094है. (स्थाई निषेधाज्ञा के अधीन)"
        )
        entries = parsing.parse_mutation_entries(html)
        entry = entries[0]
        self.assertTrue(entry["is_litigation_flagged"])
        self.assertIn("स्थाई निषेधाज्ञा", entry["matched_litigation_keywords"])
        # Flagging must not suppress normal field extraction.
        self.assertEqual(entry["case_number"], "7700/25")
        self.assertIn("राजीव मित्तल", entry["buyer_name"])

    def test_bare_case_number_is_not_a_litigation_false_positive(self):
        html = (
            '<td class="order-text"><span>वाद सं.1234/25 खाता सं. 222 खसरा न. 1 '
            "रकबा 10 वर्गमीटर विक्रेता क पुत्र ख नि. ग का नाम खारिज होकर क्रेता घ "
            "पुत्र ङ नि. च का नाम दर्ज होवे. बैनामा 100000/-01.01.2025</span></td>"
        )
        entries = parsing.parse_mutation_entries(html)
        self.assertFalse(entries[0]["is_litigation_flagged"])


class BhulekhClientTests(TestCase):
    def _mock_session(self, json_payload):
        session = MagicMock()
        response = MagicMock()
        response.json.return_value = json_payload
        response.raise_for_status.return_value = None
        session.post.return_value = response
        return session

    def test_fetch_villages_returns_both_collision_villages_distinctly(self):
        session = self._mock_session(
            [
                {
                    "vname": "डांडालखौण्ड",
                    "village_code_census": "045187",
                    "pname": "परवादून",
                    "pargana_code_new": "60042",
                    "flg_chakbandi": "N",
                    "flg_survey": "N",
                },
                {
                    "vname": "चक डांडालखौंड",
                    "village_code_census": "800889",
                    "pname": "परवादून",
                    "pargana_code_new": "60042",
                    "flg_chakbandi": "N",
                    "flg_survey": "N",
                },
            ]
        )
        villages = bhulekh.fetch_villages(session, "060", "00304")
        self.assertEqual(len(villages), 2)
        codes = {v["code"] for v in villages}
        self.assertEqual(codes, {"045187", "800889"})

    def test_lookup_khasra_no_match_returns_none(self):
        session = self._mock_session([])
        self.assertIsNone(bhulekh.lookup_khasra(session, "999", "045187"))

    def test_request_error_wrapped(self):
        import requests

        session = MagicMock()
        session.post.side_effect = requests.ConnectionError("boom")
        with self.assertRaises(bhulekh.BhulekhRequestError):
            bhulekh.fetch_districts(session)


class RiskLevelTests(TestCase):
    def _report(self, status=TitleCheckReport.Status.COMPLETE):
        return TitleCheckReport.objects.create(status=status)

    def test_yellow_when_khasra_not_found(self):
        # A NOT_FOUND result is real data from Bhulekh, not a technical
        # failure — must be Yellow, not Gray.
        report = self._report()
        KhasraEntry.objects.create(
            report=report, khasra_number="1", lookup_status=KhasraEntry.LookupStatus.NOT_FOUND
        )
        self.assertEqual(risk.compute_risk_level(report), TitleCheckReport.RiskLevel.YELLOW)

    def test_gray_when_run_failed(self):
        report = self._report(status=TitleCheckReport.Status.FAILED)
        self.assertEqual(risk.compute_risk_level(report), TitleCheckReport.RiskLevel.GRAY)

    def test_gray_when_nothing_attempted(self):
        report = self._report()
        KhasraEntry.objects.create(
            report=report, khasra_number="1", lookup_status=KhasraEntry.LookupStatus.PENDING
        )
        self.assertEqual(risk.compute_risk_level(report), TitleCheckReport.RiskLevel.GRAY)

    def test_orange_on_lookup_error(self):
        report = self._report()
        KhasraEntry.objects.create(
            report=report, khasra_number="1", lookup_status=KhasraEntry.LookupStatus.FOUND,
            area_match=None, seller_match=True, buyer_match=True,
        )
        KhasraEntry.objects.create(
            report=report, khasra_number="2", lookup_status=KhasraEntry.LookupStatus.ERROR
        )
        self.assertEqual(risk.compute_risk_level(report), TitleCheckReport.RiskLevel.ORANGE)

    def test_red_on_mismatch(self):
        report = self._report()
        KhasraEntry.objects.create(
            report=report, khasra_number="1", lookup_status=KhasraEntry.LookupStatus.FOUND,
            seller_match=False,
        )
        self.assertEqual(risk.compute_risk_level(report), TitleCheckReport.RiskLevel.RED)

    def test_green_on_clean_confirmed_match(self):
        report = self._report()
        KhasraEntry.objects.create(
            report=report, khasra_number="1", lookup_status=KhasraEntry.LookupStatus.FOUND,
            area_match=True, seller_match=True, buyer_match=True,
        )
        self.assertEqual(risk.compute_risk_level(report), TitleCheckReport.RiskLevel.GREEN)


class NameMatchingTests(TestCase):
    """Real strings that exposed a false-mismatch bug: an AI-extracted
    name (honorific prefix, spelled-out address) vs. the same person's
    name from a live Bhulekh mutation record (no honorific, nukta variant,
    embedded zero-width joiners from OCR/data-entry, abbreviated address,
    and a ष/श consonant transliteration swap). These should match."""

    AI_EXTRACTED_SELLER = (
        "श्री पुरण सिंह राणा पुत्र श्री पुष्य सिंह राणा निवासी 74, कुमराड़ा, "
        "बल्दोगी, तहसील चिन्याली सौड़, उत्तरकाशी, उत्तराखण्ड"
    )
    BHULEKH_SELLER = (
        "पुरण सिंह राणा पुत्र पुश्‍य सिंह राणा नि. 74 कुमराडा, "
        "बल्‍डोगी तहसील चिन्‍याली सौड उत्‍तरकाशी"
    )

    def test_same_person_different_transcription_matches(self):
        self.assertTrue(services._names_match(self.AI_EXTRACTED_SELLER, self.BHULEKH_SELLER))

    def test_genuinely_different_name_does_not_match(self):
        self.assertFalse(
            services._names_match(self.AI_EXTRACTED_SELLER, "सुरेश कुमार पुत्र रमेश चन्द्र निवासी दिल्ली")
        )

    def test_blank_either_side_is_not_comparable(self):
        self.assertIsNone(services._names_match("", self.BHULEKH_SELLER))
        self.assertIsNone(services._names_match(self.AI_EXTRACTED_SELLER, ""))


class RunCheckServiceTests(TestCase):
    @patch("duediligence.services.bhulekh.fetch_khata_report_html")
    @patch("duediligence.services.bhulekh.lookup_khasra")
    @patch("duediligence.services.bhulekh.new_session")
    def test_khata_dedup_and_field_matching(self, mock_new_session, mock_lookup, mock_fetch):
        mock_new_session.return_value = MagicMock()
        # Both khasras resolve to the same khata — must trigger exactly
        # one fetch_khata_report_html call, not two.
        mock_lookup.side_effect = [
            {"khata_number": "222", "khasra_number": "410", "unique_gata_id": "x"},
            {"khata_number": "222", "khasra_number": "409", "unique_gata_id": "y"},
        ]
        mock_fetch.return_value = REAL_ENTRY_HTML

        report = TitleCheckReport.objects.create(
            village_code="045187",
            # Bhulekh returns names in Devanagari, so the declared name
            # must be too for the comparison to mean anything — see
            # services._names_match's docstring.
            seller_name="पुरण सिंह राणा",
            buyer_name="राजीव मित्तल",
            deed_area_value=Decimal("94.23"),
            deed_area_unit=TitleCheckReport.DeedAreaUnit.SQM,
        )
        KhasraEntry.objects.create(report=report, khasra_number="410", order=0)
        KhasraEntry.objects.create(report=report, khasra_number="409", order=1)

        services.run_check(report)
        report.refresh_from_db()

        self.assertEqual(mock_fetch.call_count, 1)  # dedup proof
        self.assertEqual(KhataLookup.objects.filter(report=report).count(), 1)
        self.assertEqual(report.status, TitleCheckReport.Status.COMPLETE)

        entries = list(report.khasra_entries.order_by("order"))
        for entry in entries:
            self.assertEqual(entry.lookup_status, KhasraEntry.LookupStatus.FOUND)
            self.assertTrue(entry.seller_match)
            self.assertTrue(entry.buyer_match)
            self.assertTrue(entry.area_match)  # 46 + 48.23 = 94.23, matches declared total
        self.assertEqual(report.risk_level, TitleCheckReport.RiskLevel.GREEN)

    @patch("duediligence.services.bhulekh.new_session")
    def test_session_failure_marks_report_failed(self, mock_new_session):
        mock_new_session.side_effect = bhulekh.BhulekhSessionError("down")
        report = TitleCheckReport.objects.create(village_code="045187")
        services.run_check(report)
        report.refresh_from_db()
        self.assertEqual(report.status, TitleCheckReport.Status.FAILED)
        self.assertEqual(report.risk_level, TitleCheckReport.RiskLevel.GRAY)
        self.assertTrue(report.last_run_error)

    @patch("duediligence.services.bhulekh.fetch_khata_report_html")
    @patch("duediligence.services.bhulekh.lookup_khasra")
    @patch("duediligence.services.bhulekh.new_session")
    def test_overlong_mutation_field_is_truncated_not_crashed(
        self, mock_new_session, mock_lookup, mock_fetch
    ):
        """bulk_create() skips full_clean() — confirmed live against a real
        khata whose mutation log had a seller/buyer name+address longer
        than MutationEntry's 200-char columns, which crashed with a raw
        StringDataRightTruncation instead of saving. seller_name here is
        400 chars, deliberately double the column's max_length."""
        mock_new_session.return_value = MagicMock()
        mock_lookup.return_value = {"khata_number": "222", "khasra_number": "410", "unique_gata_id": "x"}
        overlong_html = REAL_ENTRY_HTML.replace(
            "पुरण सिंह राणा पुत्र पुश्य सिंह राणा नि. 74 कुमराडा,", "अ" * 400
        )
        mock_fetch.return_value = overlong_html

        report = TitleCheckReport.objects.create(village_code="045187")
        KhasraEntry.objects.create(report=report, khasra_number="410", order=0)

        services.run_check(report)  # must not raise
        report.refresh_from_db()

        self.assertEqual(report.status, TitleCheckReport.Status.COMPLETE)
        entry = MutationEntry.objects.get(khata_lookup__report=report)
        self.assertEqual(len(entry.seller_name), 200)  # truncated to fit, not dropped
        self.assertGreater(len(entry.raw_text), 200)  # full text still preserved


class AdminAccessControlTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.report = TitleCheckReport.objects.create(village_code="045187")

    def test_anonymous_redirected_to_login(self):
        url = reverse("admin:duediligence_titlecheckreport_report", args=[self.report.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response.url)

    def test_anonymous_cannot_trigger_extraction(self):
        url = reverse("admin:duediligence_titlecheckreport_extract_ai", args=[self.report.pk])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response.url)

    def test_non_staff_forbidden(self):
        get_user_model().objects.create_user(username="nobody", password="pw", is_staff=False)
        self.client.login(username="nobody", password="pw")
        url = reverse("admin:duediligence_titlecheckreport_report", args=[self.report.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)  # admin_view() redirects non-staff to login too

    def test_staff_can_view_report(self):
        get_user_model().objects.create_superuser(
            username="staffer", password="pw", email="staffer@example.com"
        )
        self.client.login(username="staffer", password="pw")
        url = reverse("admin:duediligence_titlecheckreport_report", args=[self.report.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)


class RunCheckQueueingTests(TestCase):
    """bhulekh.uk.gov.in blocks this server's own network (confirmed from
    both Render and GitHub Actions) — 'Run Bhulekh Check' can only queue
    the report for poll_bhulekh_queue (running elsewhere) to pick up, never
    call services.run_check() itself."""

    def setUp(self):
        self.client = Client()
        get_user_model().objects.create_superuser(
            username="staffer", password="pw", email="staffer@example.com"
        )
        self.client.login(username="staffer", password="pw")
        self.report = TitleCheckReport.objects.create(
            village_code="045187", last_run_error="stale error from a previous run"
        )

    @patch("duediligence.admin.services.run_check")
    def test_run_check_view_queues_instead_of_running(self, mock_run_check):
        url = reverse("admin:duediligence_titlecheckreport_run_check", args=[self.report.pk])
        response = self.client.post(url)

        mock_run_check.assert_not_called()
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, TitleCheckReport.Status.QUEUED)
        self.assertEqual(self.report.last_run_error, "")
        self.assertRedirects(
            response,
            reverse("admin:duediligence_titlecheckreport_change", args=[self.report.pk]),
        )

    def test_run_check_view_rejects_get(self):
        url = reverse("admin:duediligence_titlecheckreport_run_check", args=[self.report.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 405)

    def test_status_endpoint_reports_current_state(self):
        self.report.status = TitleCheckReport.Status.QUEUED
        self.report.save()
        url = reverse("admin:duediligence_titlecheckreport_status", args=[self.report.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "queued")

    def test_status_endpoint_requires_staff(self):
        self.client.logout()
        url = reverse("admin:duediligence_titlecheckreport_status", args=[self.report.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    def test_change_form_renders_with_queued_status(self):
        self.report.status = TitleCheckReport.Status.QUEUED
        self.report.save()
        url = reverse("admin:duediligence_titlecheckreport_change", args=[self.report.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Bhulekh check queued")
        self.assertContains(response, "DUEDILIGENCE_STATUS_POLL_URL")


class PollBhulekhQueueCommandTests(TestCase):
    def setUp(self):
        self.queued = TitleCheckReport.objects.create(
            village_code="045187", status=TitleCheckReport.Status.QUEUED
        )
        self.draft = TitleCheckReport.objects.create(
            village_code="045187", status=TitleCheckReport.Status.DRAFT
        )

    @patch("duediligence.management.commands.poll_bhulekh_queue.services.run_check")
    def test_once_processes_only_queued_reports(self, mock_run_check):
        from io import StringIO

        from django.core.management import call_command

        call_command("poll_bhulekh_queue", "--once", stdout=StringIO())

        mock_run_check.assert_called_once_with(self.queued)

    @patch("duediligence.management.commands.poll_bhulekh_queue.services.run_check")
    def test_once_with_empty_queue_returns_immediately(self, mock_run_check):
        from io import StringIO

        from django.core.management import call_command

        self.queued.status = TitleCheckReport.Status.DRAFT
        self.queued.save()

        call_command("poll_bhulekh_queue", "--once", stdout=StringIO())

        mock_run_check.assert_not_called()

    @patch("duediligence.management.commands.poll_bhulekh_queue.services.run_check")
    def test_one_bad_report_does_not_stop_the_others(self, mock_run_check):
        from io import StringIO

        from django.core.management import call_command

        TitleCheckReport.objects.create(
            village_code="045187", status=TitleCheckReport.Status.QUEUED
        )
        mock_run_check.side_effect = [RuntimeError("boom"), None]

        call_command("poll_bhulekh_queue", "--once", stdout=StringIO(), stderr=StringIO())

        self.assertEqual(mock_run_check.call_count, 2)


class DeedAiClientTests(TestCase):
    @override_settings(AI_EXTRACTION_PROVIDER="anthropic", ANTHROPIC_API_KEY="")
    def test_config_error_when_anthropic_key_blank(self):
        with self.assertRaises(deed_ai.DeedExtractionConfigError):
            deed_ai.extract_deed_fields(b"fake-pdf-bytes")

    @override_settings(AI_EXTRACTION_PROVIDER="openai", OPENAI_API_KEY="")
    def test_config_error_when_openai_key_blank(self):
        with self.assertRaises(deed_ai.DeedExtractionConfigError):
            deed_ai.extract_deed_fields(b"fake-pdf-bytes")

    @override_settings(AI_EXTRACTION_PROVIDER="carrier-pigeon")
    def test_config_error_on_unrecognized_provider(self):
        with self.assertRaises(deed_ai.DeedExtractionConfigError):
            deed_ai.extract_deed_fields(b"fake-pdf-bytes")

    @override_settings(AI_EXTRACTION_PROVIDER="anthropic", ANTHROPIC_API_KEY="test-key")
    @patch("duediligence.deed_ai._build_model")
    def test_request_error_wrapped(self, mock_build_model):
        mock_model = MagicMock()
        mock_model.invoke.side_effect = RuntimeError("network boom")
        mock_build_model.return_value = mock_model
        with self.assertRaises(deed_ai.DeedExtractionRequestError):
            deed_ai.extract_deed_fields(b"fake-pdf-bytes")

    @patch("duediligence.deed_ai._build_model")
    def test_parse_error_when_schema_validation_fails(self, mock_build_model):
        mock_model = MagicMock()
        mock_model.invoke.return_value = {"parsed": None, "parsing_error": "bad json"}
        mock_build_model.return_value = mock_model
        with self.assertRaises(deed_ai.DeedExtractionParseError):
            deed_ai.extract_deed_fields(b"fake-pdf-bytes")

    @patch("duediligence.deed_ai._build_model")
    def test_successful_extraction_returns_parsed_and_raw_json(self, mock_build_model):
        schema = deed_ai.DeedExtractionSchema(
            seller_name="पुरण सिंह राणा", buyer_name="राजीव मित्तल", khasra_numbers=["410", "409"]
        )
        mock_model = MagicMock()
        mock_model.invoke.return_value = {"parsed": schema, "parsing_error": None}
        mock_build_model.return_value = mock_model
        parsed, raw_json = deed_ai.extract_deed_fields(b"fake-pdf-bytes")
        self.assertEqual(parsed.seller_name, "पुरण सिंह राणा")
        self.assertIn("राजीव मित्तल", raw_json)


TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="duediligence-test-media-")


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class ExtractDeedServiceTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def _report_with_pdf(self, **kwargs):
        pdf = SimpleUploadedFile("deed.pdf", b"%PDF-1.4 fake content", content_type="application/pdf")
        return TitleCheckReport.objects.create(deed_pdf=pdf, **kwargs)

    def test_no_pdf_sets_error(self):
        report = TitleCheckReport.objects.create()
        services.extract_deed(report)
        report.refresh_from_db()
        self.assertEqual(report.extraction_status, TitleCheckReport.ExtractionStatus.ERROR)
        self.assertIn("No deed PDF", report.extraction_error)

    def test_pdf_read_failure_sets_error_status_instead_of_raising(self):
        report = self._report_with_pdf()
        with patch.object(TitleCheckReport.deed_pdf.field.attr_class, "open", side_effect=OSError("connection reset")):
            services.extract_deed(report)  # must not raise
        report.refresh_from_db()
        self.assertEqual(report.extraction_status, TitleCheckReport.ExtractionStatus.ERROR)
        self.assertIn("connection reset", report.extraction_error)

    @patch("duediligence.services.deed_ai.extract_deed_fields")
    def test_extraction_error_sets_error_status(self, mock_extract):
        mock_extract.side_effect = deed_ai.DeedExtractionRequestError("timed out")
        report = self._report_with_pdf()
        services.extract_deed(report)
        report.refresh_from_db()
        self.assertEqual(report.extraction_status, TitleCheckReport.ExtractionStatus.ERROR)
        self.assertIn("timed out", report.extraction_error)

    @patch("duediligence.services.deed_ai.extract_deed_fields")
    def test_success_populates_fields_and_creates_khasra_rows(self, mock_extract):
        schema = deed_ai.DeedExtractionSchema(
            seller_name="पुरण सिंह राणा",
            buyer_name="राजीव मित्तल",
            deed_date="2025-06-02",
            consideration_amount="1800000",
            deed_area_value="94.23",
            deed_area_unit="sqm",
            khasra_numbers=["410", "409"],
            confidence_notes="all clear",
        )
        mock_extract.return_value = (schema, '{"seller_name": "..."}')
        report = self._report_with_pdf()
        KhasraEntry.objects.create(report=report, khasra_number="409", order=0)  # pre-existing

        services.extract_deed(report)
        report.refresh_from_db()

        self.assertEqual(report.extraction_status, TitleCheckReport.ExtractionStatus.COMPLETE)
        self.assertEqual(report.seller_name, "पुरण सिंह राणा")
        self.assertEqual(report.buyer_name, "राजीव मित्तल")
        self.assertEqual(report.deed_date, date(2025, 6, 2))
        self.assertEqual(report.consideration_amount, Decimal("1800000"))
        self.assertEqual(report.deed_area_value, Decimal("94.23"))
        self.assertEqual(report.extraction_notes, "all clear")

        khasra_numbers = set(report.khasra_entries.values_list("khasra_number", flat=True))
        self.assertEqual(khasra_numbers, {"409", "410"})
        self.assertEqual(report.khasra_entries.count(), 2)  # no duplicate for the pre-existing "409"

    @patch("duediligence.services.deed_ai.extract_deed_fields")
    def test_blank_extracted_field_does_not_overwrite_existing_value(self, mock_extract):
        schema = deed_ai.DeedExtractionSchema(seller_name=None, buyer_name="राजीव मित्तल")
        mock_extract.return_value = (schema, "{}")
        report = self._report_with_pdf(seller_name="Already Typed By Admin")

        services.extract_deed(report)
        report.refresh_from_db()

        self.assertEqual(report.seller_name, "Already Typed By Admin")  # untouched
        self.assertEqual(report.buyer_name, "राजीव मित्तल")  # overwritten
