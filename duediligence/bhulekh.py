"""Thin HTTP client for the Uttarakhand Bhulekh Public ROR portal.

Pure I/O module — no Django model imports, fully mockable via
unittest.mock.patch("duediligence.bhulekh.requests.Session").

The call sequence below was reverse-engineered by hand (curl) against the
live portal and verified against a real registered sale deed (Khasra 409 &
410, Khata 222, Village डांडालखौण्ड, Tehsil/District Dehradun) — see
docs/SOURCE_RESEARCH.md for the source verification and configs/sources.yaml
for the registry entry. It is a plain session-cookie + JSON AJAX API, not a
stateful ASP.NET postback form, and (on this endpoint) has no CAPTCHA.

One requests.Session() must be created once per "Run Check" and reused for
every call below — a fresh session per call breaks the sequence, since step
1 is what issues the JSESSIONID cookie every later call depends on.
"""

import re
import ssl

import requests
from requests.adapters import HTTPAdapter

BASE_URL = "https://bhulekh.uk.gov.in/public/public_ror/"
ENTRY_URL = BASE_URL + "Public_ROR.jsp"
ACTION_URL = BASE_URL + "action/public_action.jsp"
REPORT_URL = BASE_URL + "public_ror_report.jsp"  # NOT under action/ — a different endpoint

FASLI_CODE = "999"
FASLI_NAME = "वर्तमान फसली"  # "current fasli year" — matches the site's own default selection
REQUEST_TIMEOUT = 15
USER_AGENT = "Mozilla/5.0 (compatible; ShivShaktiDueDiligence/1.0)"


class BhulekhError(Exception):
    """Base class for anything that goes wrong talking to Bhulekh."""


class BhulekhSessionError(BhulekhError):
    """Step 1 (establishing the JSESSIONID cookie) failed."""


class BhulekhRequestError(BhulekhError):
    """A subsequent POST failed, returned a non-200, or wasn't valid JSON."""


class _LegacySSLAdapter(HTTPAdapter):
    """bhulekh.uk.gov.in's TLS setup requires legacy SSL renegotiation,
    which OpenSSL 3.x disables by default as a hardening measure — plain
    requests.Session() fails with SSLError: UNSAFE_LEGACY_RENEGOTIATION_DISABLED.
    This re-enables just that one flag (SSL_OP_LEGACY_SERVER_CONNECT) for
    connections to this specific host."""

    def _legacy_context(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        ctx.options |= 0x4  # SSL_OP_LEGACY_SERVER_CONNECT
        return ctx

    def init_poolmanager(self, *args, **kwargs):
        kwargs["ssl_context"] = self._legacy_context()
        return super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, *args, **kwargs):
        kwargs["ssl_context"] = self._legacy_context()
        return super().proxy_manager_for(*args, **kwargs)


def new_session() -> requests.Session:
    """Step 1: GET Public_ROR.jsp to obtain a JSESSIONID cookie."""
    session = requests.Session()
    session.mount("https://", _LegacySSLAdapter())
    session.headers.update({"User-Agent": USER_AGENT})
    try:
        response = session.get(ENTRY_URL, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise BhulekhSessionError(
            "Could not establish a session with the Bhulekh portal."
        ) from exc
    return session


def _post_json(session: requests.Session, url: str, data: dict) -> list | dict:
    try:
        response = session.post(url, data=data, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise BhulekhRequestError(
            f"Request to Bhulekh failed: {data.get('act', url)}"
        ) from exc
    try:
        return response.json()
    except ValueError as exc:
        # Bhulekh occasionally returns an HTML error page with a 200
        # status on backend errors — guard against that here rather
        # than let a JSONDecodeError leak out as a generic 500.
        raise BhulekhRequestError(
            f"Bhulekh returned a non-JSON response for: {data.get('act', url)}"
        ) from exc


def fetch_districts(session: requests.Session) -> list[dict]:
    """POST act=fillDistrict. Returns every district verbatim."""
    data = _post_json(session, ACTION_URL, {"act": "fillDistrict"})
    if not isinstance(data, list):
        return []
    return [
        {
            "code": d.get("district_code_census", ""),
            "name": d.get("district_name", ""),
            "name_english": d.get("district_name_english", ""),
        }
        for d in data
    ]


def fetch_tehsils(session: requests.Session, district_code: str) -> list[dict]:
    """POST act=fillTehsil&district_code=<code>."""
    data = _post_json(
        session, ACTION_URL, {"act": "fillTehsil", "district_code": district_code}
    )
    if not isinstance(data, list):
        return []
    return [
        {
            "code": t.get("tehsil_code_census", ""),
            "name": t.get("tehsil_name", ""),
            "name_english": t.get("tehsil_name_english", ""),
        }
        for t in data
    ]


def fetch_villages(
    session: requests.Session, district_code: str, tehsil_code: str
) -> list[dict]:
    """POST act=fillVillage&district_code=&tehsil_code=.

    Returns EVERY row verbatim, no fuzzy/first-match dedup by name. Bhulekh
    can (and does — confirmed live) return multiple similarly-named
    villages under the same district/tehsil (e.g. "डांडालखौण्ड" and
    "चक डांडालखौंड"), and picking the wrong one silently returns
    plausible-but-wrong data for a completely different plot. This
    function must never collapse or guess — the caller (the AJAX view /
    picker UI) shows the full list, pargana name included, so a human
    always makes the final disambiguation.
    """
    data = _post_json(
        session,
        ACTION_URL,
        {"act": "fillVillage", "district_code": district_code, "tehsil_code": tehsil_code},
    )
    if not isinstance(data, list):
        return []
    return [
        {
            "code": v.get("village_code_census", ""),
            "name": v.get("vname", ""),
            "pargana_name": v.get("pname", ""),
            "pargana_code": v.get("pargana_code_new", ""),
            "flg_chakbandi": v.get("flg_chakbandi", ""),
            "flg_survey": v.get("flg_survey", ""),
        }
        for v in data
    ]


def lookup_khasra(
    session: requests.Session, khasra_number: str, village_code: str
) -> dict | None:
    """POST act=sbksn (search by khasra number). Returns the first match
    {"khata_number", "khasra_number", "unique_gata_id"}, or None if the
    portal's JSON array is empty (no record for this khasra)."""
    data = _post_json(
        session,
        ACTION_URL,
        {
            "act": "sbksn",
            "kcn": khasra_number,
            "vcc": village_code,
            "fasli-code-value": FASLI_CODE,
            "fasli-name-value": FASLI_NAME,
        },
    )
    if not isinstance(data, list) or not data:
        return None
    match = data[0]
    return {
        "khata_number": match.get("khata_number", ""),
        "khasra_number": match.get("khasra_number", ""),
        "unique_gata_id": match.get("unique_gata_id", ""),
    }


def fetch_khata_report_html(
    session: requests.Session,
    *,
    district_name: str,
    district_code: str,
    tehsil_name: str,
    tehsil_code: str,
    village_name: str,
    village_code: str,
    pargana_name: str,
    pargana_code: str,
    khata_number: str,
) -> str:
    """POST public_ror_report.jsp — the full Record of Rights report,
    including the chronological mutation-history log. khata_number is
    zero-padded to 5 digits HERE (e.g. "222" -> "00222"); nothing
    upstream of this function should pad it."""
    padded_khata = re.sub(r"\D", "", khata_number).zfill(5)
    payload = {
        "district_name": district_name,
        "district_code": district_code,
        "tehsil_name": tehsil_name,
        "tehsil_code": tehsil_code,
        "village_name": village_name,
        "village_code": village_code,
        "pargana_name": pargana_name,
        "pargana_code": pargana_code,
        "fasli_code": FASLI_CODE,
        "fasli_name": FASLI_NAME,
        "khata_number": padded_khata,
    }
    try:
        response = session.post(REPORT_URL, data=payload, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise BhulekhRequestError(
            f"Fetching the ROR report for khata {khata_number} failed."
        ) from exc
    return response.text
