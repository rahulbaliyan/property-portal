"""Orchestration: wires bhulekh.py (HTTP) and parsing.py (regex) into the
Django models, and is the one place that touches both the pure client/parser
layer and persistence. Keeps bhulekh.py/parsing.py independently testable
without Django models, and keeps this module's own tests mockable at the
bhulekh.* boundary rather than needing real HTTP calls.
"""

import difflib
import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from . import bhulekh, deed_ai, risk
from .models import KhasraEntry, KhataLookup, MutationEntry, TitleCheckReport

AREA_MATCH_TOLERANCE = Decimal("0.02")  # ±2%

_AREA_ENTRY_RE = re.compile(r"([^:;]+):\s*([\d.]+)\s*(वर्ग\s?मीटर|वर्ग\s?मी\.?)")


def run_check(report: TitleCheckReport) -> None:
    """The single entrypoint the admin view calls. Never raises out to the
    caller — failures are recorded on the report itself (status, risk_level,
    last_run_error) so they're visible in the UI rather than surfacing as
    an unhandled exception."""
    try:
        session = bhulekh.new_session()
    except bhulekh.BhulekhError as exc:
        report.status = TitleCheckReport.Status.FAILED
        report.last_run_error = str(exc)
        report.risk_level = TitleCheckReport.RiskLevel.GRAY
        report.last_run_at = timezone.now()
        report.save()
        return

    entries = list(report.khasra_entries.order_by("order", "id"))
    khata_lookups_by_number: dict[str, KhataLookup] = {}

    for entry in entries:
        try:
            match = bhulekh.lookup_khasra(session, entry.khasra_number, report.village_code)
        except bhulekh.BhulekhError as exc:
            entry.lookup_status = KhasraEntry.LookupStatus.ERROR
            entry.lookup_error_message = str(exc)
            entry.save()
            continue

        if match is None:
            entry.lookup_status = KhasraEntry.LookupStatus.NOT_FOUND
            entry.save()
            continue

        entry.lookup_status = KhasraEntry.LookupStatus.FOUND
        entry.matched_khata_number = match["khata_number"]
        entry.save()

        khata_number = match["khata_number"]
        if khata_number not in khata_lookups_by_number:
            khata_lookups_by_number[khata_number] = _fetch_and_store_khata(
                session, report, khata_number
            )

    # Second pass: now that every distinct khata has been fetched once,
    # point each khasra entry at its snapshot and compute match flags.
    matched_areas = []
    for entry in entries:
        if entry.lookup_status != KhasraEntry.LookupStatus.FOUND:
            continue
        khata_lookup = khata_lookups_by_number.get(entry.matched_khata_number)
        if khata_lookup is None or khata_lookup.fetch_status != KhataLookup.FetchStatus.OK:
            continue

        entry.khata_lookup = khata_lookup
        latest = _find_latest_mutation_for_khasra(khata_lookup, entry.khasra_number)
        if latest is not None:
            entry.latest_mutation_case_number = latest.case_number
            entry.latest_mutation_order_date = latest.order_date
            entry.latest_mutation_seller_name = latest.seller_name
            entry.latest_mutation_buyer_name = latest.buyer_name
            entry.latest_mutation_area_text = latest.area_text
            entry.latest_mutation_deed_amount_text = latest.deed_amount_text
            entry.latest_mutation_deed_date = latest.deed_date
            entry.latest_mutation_raw_text = latest.raw_text

            entry.seller_match = _names_match(report.seller_name, latest.seller_name)
            entry.buyer_match = _names_match(report.buyer_name, latest.buyer_name)

            area = _parse_area_sqm_for_khasra(latest.area_text, entry.khasra_number)
            if area is not None:
                matched_areas.append(area)
        entry.save()

    _apply_area_match(report, entries, matched_areas)
    for entry in entries:
        entry.save()

    report.status = TitleCheckReport.Status.COMPLETE
    report.risk_level = risk.compute_risk_level(report)
    report.last_run_at = timezone.now()
    report.last_run_error = ""
    report.save()


def _fetch_and_store_khata(session, report: TitleCheckReport, khata_number: str) -> KhataLookup:
    try:
        raw_html = bhulekh.fetch_khata_report_html(
            session,
            district_name=report.district_name,
            district_code=report.district_code,
            tehsil_name=report.tehsil_name,
            tehsil_code=report.tehsil_code,
            village_name=report.village_name,
            village_code=report.village_code,
            pargana_name=report.pargana_name,
            pargana_code=report.pargana_code,
            khata_number=khata_number,
        )
    except bhulekh.BhulekhError as exc:
        return KhataLookup.objects.create(
            report=report,
            khata_number=khata_number,
            fetch_status=KhataLookup.FetchStatus.ERROR,
            fetch_error_message=str(exc),
        )

    khata_lookup = KhataLookup.objects.create(
        report=report,
        khata_number=khata_number,
        fetch_status=KhataLookup.FetchStatus.OK,
        raw_report_html=raw_html,
    )

    from .parsing import parse_mutation_entries

    parsed_entries = parse_mutation_entries(raw_html)
    MutationEntry.objects.bulk_create(
        MutationEntry(khata_lookup=khata_lookup, **fields) for fields in parsed_entries
    )
    return khata_lookup


def _find_latest_mutation_for_khasra(khata_lookup: KhataLookup, khasra_number: str):
    for mutation in khata_lookup.mutation_entries.order_by("-sequence"):
        tokens = [t.strip() for t in mutation.khasra_numbers_in_entry.split(",")]
        if khasra_number.strip() in tokens:
            return mutation
    return None


_HONORIFIC_RE = re.compile(r"(श्री|श्रीमती|कुमारी|स्व[०.]?)\s*")
_ADDRESS_MARKER_RE = re.compile(r"(निवासी|नि[०.]|\bनि\b)")
# Two independently-transcribed Hindi sources for the same real name
# routinely differ in ways that don't change who's being named: an
# honorific one source includes and the other doesn't, a nukta-diacritic
# spelling variant (ड़/ड), stray zero-width joiners from OCR/data-entry
# (seen firsthand in real Bhulekh output), or a common consonant swap in
# transliteration (ष/श). NAME_MATCH_THRESHOLD is calibrated against a real
# case: the same person's name from AI deed-extraction vs. from Bhulekh's
# mutation record scored 0.97 despite an honorific, a nukta variant, and a
# ष/श swap all being present at once; an unrelated name against the same
# text scored 0.4 — the gap is wide enough for 0.85 to be a safe cutoff.
NAME_MATCH_THRESHOLD = 0.85


def _normalize_name(name: str) -> str:
    """Strips formatting noise that varies between sources without
    changing identity: zero-width joiners, nukta diacritics (decomposed
    then dropped — NOT a blanket combining-mark strip, since that would
    also destroy the virama and change the text's actual pronunciation),
    honorifics, and whitespace/case."""
    if not name:
        return ""
    name = name.replace("‍", "").replace("‌", "")
    name = unicodedata.normalize("NFKD", name).replace("़", "")
    name = unicodedata.normalize("NFC", name)
    name = re.sub(r"\s+", " ", name).strip().casefold()
    return _HONORIFIC_RE.sub("", name).strip()


def _name_core(normalized_name: str) -> str:
    """Truncates at the first address marker — the address is the part
    most likely to differ in level of detail between an AI extraction
    (which tends to include the full printed address) and Bhulekh's
    leaner mutation-log phrasing, and isn't needed to confirm identity
    once the name + parentage already matches."""
    return _ADDRESS_MARKER_RE.split(normalized_name)[0].strip().rstrip(",").strip()


def _names_match(declared: str, found: str) -> bool | None:
    """Known v1 limitation: Bhulekh always returns names in Devanagari.
    If the admin types the declared seller/buyer name in Roman script
    (e.g. copied from an English-language PAN card) instead of the
    Devanagari spelling used on the deed itself, this will never match —
    there's no transliteration here. Type the name as it appears on the
    Hindi-language deed for a meaningful comparison."""
    declared_norm, found_norm = _normalize_name(declared), _normalize_name(found)
    if not declared_norm or not found_norm:
        return None
    if declared_norm == found_norm:
        return True
    if declared_norm in found_norm or found_norm in declared_norm:
        return True
    declared_core, found_core = _name_core(declared_norm), _name_core(found_norm)
    ratio = difflib.SequenceMatcher(None, declared_core, found_core).ratio()
    return ratio >= NAME_MATCH_THRESHOLD


def _parse_area_sqm_for_khasra(area_text: str, khasra_number: str) -> Decimal | None:
    """area_text looks like '410: 46 वर्गमीटर; 409: 48.23 वर्गमीटर'. Only
    parses वर्गमीटर (sq. metres) values — v1 deliberately does not attempt
    unit conversion from hectares/other units, to avoid a wrong silent
    conversion producing false confidence."""
    for match in _AREA_ENTRY_RE.finditer(area_text or ""):
        khasra_token, value_str, _unit = match.groups()
        if khasra_token.strip() == khasra_number.strip():
            try:
                return Decimal(value_str)
            except InvalidOperation:
                return None
    return None


def _apply_area_match(report: TitleCheckReport, entries: list[KhasraEntry], matched_areas: list[Decimal]) -> None:
    """Compares the SUM of every khasra's parsed area against the deed's
    single declared total area (a report's khasras are all sold together
    on one deed, so their areas add up to the deed's total — exactly how
    a human would check it by hand). Only attempted when the deed area is
    in sq. metres and every FOUND khasra contributed a parseable area;
    otherwise area_match stays None (not comparable) on every entry rather
    than guessing from partial data."""
    found_entries = [e for e in entries if e.lookup_status == KhasraEntry.LookupStatus.FOUND]
    if (
        report.deed_area_unit != TitleCheckReport.DeedAreaUnit.SQM
        or report.deed_area_value is None
        or not found_entries
        or len(matched_areas) != len(found_entries)
    ):
        return

    total = sum(matched_areas)
    tolerance = report.deed_area_value * AREA_MATCH_TOLERANCE
    is_match = abs(total - report.deed_area_value) <= tolerance
    for entry in found_entries:
        entry.area_match = is_match


# Extracted-field name -> TitleCheckReport attribute name, for the
# straightforward CharField copies. Fields needing type conversion
# (dates, decimals, the area-unit choice) are handled separately below.
_DIRECT_COPY_FIELDS = (
    "seller_name",
    "buyer_name",
    "registration_number",
    "book_number",
    "volume_number",
    "page_number",
)


def extract_deed(report: TitleCheckReport) -> None:
    """The entrypoint the admin's "Extract with AI" button calls. Never
    raises to the caller — mirrors run_check()'s defensive shape. Only
    copies fields the model actually returned a value for, so a field the
    admin already corrected by hand isn't silently blanked out by a
    re-run."""
    if not report.deed_pdf:
        report.extraction_status = TitleCheckReport.ExtractionStatus.ERROR
        report.extraction_error = "No deed PDF uploaded."
        report.extracted_at = timezone.now()
        report.save()
        return

    with report.deed_pdf.open("rb") as f:
        pdf_bytes = f.read()

    try:
        extracted, raw_json = deed_ai.extract_deed_fields(pdf_bytes)
    except deed_ai.DeedExtractionError as exc:
        report.extraction_status = TitleCheckReport.ExtractionStatus.ERROR
        report.extraction_error = str(exc)
        report.extracted_at = timezone.now()
        report.save()
        return

    for field in _DIRECT_COPY_FIELDS:
        value = getattr(extracted, field)
        if value:
            setattr(report, field, value)

    if extracted.deed_date:
        try:
            report.deed_date = date.fromisoformat(extracted.deed_date)
        except ValueError:
            pass  # left as-is; noted in confidence_notes / raw response for review

    if extracted.consideration_amount:
        try:
            report.consideration_amount = Decimal(extracted.consideration_amount)
        except InvalidOperation:
            pass

    if extracted.deed_area_value:
        try:
            report.deed_area_value = Decimal(extracted.deed_area_value)
        except InvalidOperation:
            pass

    if extracted.deed_area_unit in TitleCheckReport.DeedAreaUnit.values:
        report.deed_area_unit = extracted.deed_area_unit

    existing_khasras = set(report.khasra_entries.values_list("khasra_number", flat=True))
    next_order = report.khasra_entries.count()
    for khasra_number in extracted.khasra_numbers:
        if khasra_number and khasra_number not in existing_khasras:
            KhasraEntry.objects.create(report=report, khasra_number=khasra_number, order=next_order)
            existing_khasras.add(khasra_number)
            next_order += 1

    report.extraction_status = TitleCheckReport.ExtractionStatus.COMPLETE
    report.extraction_raw_response = raw_json
    report.extraction_notes = extracted.confidence_notes
    report.extracted_at = timezone.now()
    report.extraction_error = ""
    report.save()
