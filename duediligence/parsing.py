"""Best-effort parser for the Bhulekh Record-of-Rights mutation-history log.

The chronological mutation log lives inside a single ``<td class="order-text">``
block in the report HTML (confirmed against a real fetch), as one long run of
text with individual entries separated by ``<br>`` tags. Entries span roughly
two decades of Hindi legal-register phrasing that has drifted over time —
amount/date formatting in particular varies a lot between older and newer
entries. This parser is deliberately best-effort: every extracted field is a
convenience index, and MutationEntry.raw_text always keeps the untouched
original block so a human can read whatever a given regex missed. Nothing
here should ever raise on unexpected input — a field that doesn't match its
pattern is simply left blank.

Worked example (real, fetched live 2026-08-16 against Khata 222, Village
डांडालखौण्ड, Dehradun):

    आ.अपर तह.सदर देहरादून वाद सं.7700/25/26.07.2025 खाता सं. 222 खसरा न. 410
    रकबा 46 वर्गमीटर व खसरा न. 409 रकबा 48.23 वर्गमीटर कुल रकबा 0.0094है. ल.
    परता से विक्रेता पुरण सिंह राणा पुत्र पुश्य सिंह राणा नि. 74 कुमराडा,
    बल्डोगी तहसील चिन्याली सौड उत्तरकाशी का नाम खारिज होकर क्रेता राजीव
    मित्तल पुत्र नरेन्द्र कुमार मित्तल नि. 171/2 इन्दु वाटिका दक्षिण सिविल
    लाईन मुजफ्फरनगर, मुजफ्फरनगर सिटी उ.प्र. का नाम दर्ज होवे. बैनामा
    1800000/-02.06.2025

parses to:

    {
        "case_number": "7700/25",
        "order_date": date(2025, 7, 26),
        "khata_number_in_entry": "222",
        "khasra_numbers_in_entry": "410,409",
        "area_text": "410: 46 वर्गमीटर; 409: 48.23 वर्गमीटर",
        "seller_name": "पुरण सिंह राणा पुत्र पुश्य सिंह राणा नि. 74 कुमराडा, बल्डोगी तहसील चिन्याली सौड उत्तरकाशी",
        "buyer_name": "राजीव मित्तल पुत्र नरेन्द्र कुमार मित्तल नि. 171/2 इन्दु वाटिका दक्षिण सिविल लाईन मुजफ्फरनगर, मुजफ्फरनगर सिटी उ.प्र.",
        "deed_amount_text": "1800000",
        "deed_date": date(2025, 6, 2),
        "is_litigation_flagged": False,
        "matched_litigation_keywords": "",
    }
"""

import html
import re
from datetime import date

# Deliberately narrow. Every routine mutation is itself filed as a revenue
# "case" (वाद) as part of the normal, unremarkable process — flagging the
# bare "वाद सं" pattern would make every single entry a false positive.
# Only specific injunction/appeal/civil-suit/stay-order language counts.
LITIGATION_KEYWORDS = [
    "स्थाई निषेधाज्ञा",  # permanent injunction
    "अस्थाई निषेधाज्ञा",  # temporary injunction
    "स्थगन आदेश",  # stay order
    "सिविल वाद",  # civil suit (distinct from a routine revenue mutation case)
    "अपील",  # appeal
    "उच्च न्यायालय",  # high court
    "निषेधाज्ञा",  # injunction (broad catch-all, in case a variant phrasing is missed above)
]

_ORDER_TEXT_BLOCK_RE = re.compile(
    r'class="order-text"[^>]*>(.*?)</td>', re.DOTALL | re.IGNORECASE
)
# Entries don't split cleanly on <br><br> — mid-entry line breaks happen
# (e.g. a date and amount separated by a stray <br><br> in older rows).
# Instead, split right before each recognizable entry-start marker, so a
# stray internal <br> stays attached to the entry it belongs to.
_ENTRY_START_RE = re.compile(r"(?=(?:वाद\s*सं[.0]|मि\.\s*नं?[.0]?\s*\d))")

_CASE_NUMBER_RE = re.compile(r"(?:वाद\s*सं[.0]?|मि\.\s*नं?[.0]?)\s*([\d]+/[\d]+)")
_ORDER_DATE_RE = re.compile(r"वाद\s*सं[.0]?\s*[\d]+/[\d]+/(\d{1,2}\.\d{1,2}\.\d{4})")
_KHATA_RE = re.compile(r"खाता\s*सं\.?\s*\(?\s*(\d+)")
_KHASRA_AREA_RE = re.compile(
    r"खसरा\s*न[ंं0.]*\s*([^\s,]+?)\s*रकबा\s*([\d.]+)\s*(वर्ग\s?मीटर|वर्ग\s?मी\.?|है[0.]*|हे[0.]*|एकड़)?"
)
_SELLER_RE = re.compile(r"विक्रेता\s+(.+?)\s+का\s*नाम\s*खारिज")
# Negative lookbehind is required: "क्रेता" (buyer) is a literal substring
# of "विक्रेता" (seller) — without it, this matches inside "विक्रेता" itself
# and captures everything from the seller's name onward instead of just
# the buyer's.
_BUYER_RE = re.compile(r"(?<!वि)क्रेता\s+(.+?)\s+का\s*नाम\s*दर्ज")
# Amount/date formatting drifts across decades of entries — try the modern
# "बैनामा <amount>/-<date>" form first, then older "बै <date> <amount>/-"
# variants. First match wins; if none match, both fields stay blank.
_DEED_AMOUNT_DATE_PATTERNS = [
    re.compile(r"बैनामा\s*([\d,]+)\s*/-\s*(\d{1,2}\.\d{1,2}\.\d{4})"),
    re.compile(r"बै\.?\s*(\d{1,2}[.\-]\d{1,2}[.\-]\d{2,4})\s*/?\s*([\d,]+)\s*/-"),
]


def _parse_date(text: str) -> date | None:
    """Accepts DD.MM.YYYY, DD-MM-YYYY, or DD.MM.YY / DD-MM-YY."""
    match = re.match(r"(\d{1,2})[.\-](\d{1,2})[.\-](\d{2,4})", text)
    if not match:
        return None
    day, month, year = match.groups()
    year = int(year)
    if year < 100:
        # Two-digit years in this register are all early-2000s entries.
        year += 2000 if year < 70 else 1900
    try:
        return date(year, int(month), int(day))
    except ValueError:
        return None


def extract_order_text_blocks(raw_html: str) -> list[str]:
    """Pulls every `class="order-text"` block out of the report HTML.
    Falls back to treating the whole document as one block if the marker
    isn't found, so a differently-structured page still produces something
    rather than silently returning nothing."""
    blocks = _ORDER_TEXT_BLOCK_RE.findall(raw_html)
    return blocks if blocks else [raw_html]


def _split_entries(block: str) -> list[str]:
    unescaped = html.unescape(block)
    text = re.sub(r"<br\s*/?>", " ", unescaped, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    parts = _ENTRY_START_RE.split(text)
    return [p.strip() for p in parts if p.strip()]


def flag_litigation(entry_text: str) -> list[str]:
    """Returns every LITIGATION_KEYWORDS hit in entry_text (possibly
    empty). Never used to suppress normal field parsing — a flagged entry
    is still parsed for its fields and surfaced separately as an excerpt
    for human review."""
    return [kw for kw in LITIGATION_KEYWORDS if kw in entry_text]


def _parse_entry(entry_text: str, sequence: int) -> dict:
    case_match = _CASE_NUMBER_RE.search(entry_text)
    case_number = case_match.group(1) if case_match else ""

    order_date = None
    order_date_match = _ORDER_DATE_RE.search(entry_text)
    if order_date_match:
        order_date = _parse_date(order_date_match.group(1))

    khata_match = _KHATA_RE.search(entry_text)
    khata_number_in_entry = khata_match.group(1) if khata_match else ""

    khasra_pairs = _KHASRA_AREA_RE.findall(entry_text)
    khasra_numbers_in_entry = ",".join(k for k, _, _ in khasra_pairs)
    area_text = "; ".join(
        f"{k}: {a} {(u or '').strip()}".strip() for k, a, u in khasra_pairs
    )

    seller_match = _SELLER_RE.search(entry_text)
    seller_name = seller_match.group(1).strip() if seller_match else ""

    buyer_match = _BUYER_RE.search(entry_text)
    buyer_name = buyer_match.group(1).strip() if buyer_match else ""

    deed_amount_text = ""
    deed_date = None
    for pattern in _DEED_AMOUNT_DATE_PATTERNS:
        match = pattern.search(entry_text)
        if not match:
            continue
        groups = match.groups()
        # The two fallback patterns capture (amount, date) or (date, amount)
        # in different orders — tell them apart by which group looks like a date.
        if re.match(r"\d{1,2}[.\-]\d{1,2}[.\-]\d{2,4}", groups[0]):
            date_str, amount_str = groups
        else:
            amount_str, date_str = groups
        deed_amount_text = amount_str.replace(",", "")
        deed_date = _parse_date(date_str)
        break

    keywords = flag_litigation(entry_text)

    return {
        "sequence": sequence,
        "raw_text": entry_text,
        "case_number": case_number,
        "order_date": order_date,
        "khata_number_in_entry": khata_number_in_entry,
        "khasra_numbers_in_entry": khasra_numbers_in_entry,
        "area_text": area_text,
        "seller_name": seller_name,
        "buyer_name": buyer_name,
        "deed_amount_text": deed_amount_text,
        "deed_date": deed_date,
        "is_litigation_flagged": bool(keywords),
        "matched_litigation_keywords": ", ".join(keywords),
    }


def parse_mutation_entries(raw_html: str) -> list[dict]:
    """Top-level entry point. Returns a list of plain dicts (not model
    instances — duediligence/services.py handles persistence), one per
    parsed mutation-history entry, in document order."""
    entries = []
    sequence = 0
    for block in extract_order_text_blocks(raw_html):
        for entry_text in _split_entries(block):
            if "खसरा" not in entry_text and "वाद" not in entry_text:
                continue
            entries.append(_parse_entry(entry_text, sequence))
            sequence += 1
    return entries
