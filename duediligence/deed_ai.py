"""LangChain-based structured extraction of deed fields from an uploaded
PDF. Pure I/O module — no Django model imports, fully mockable via
unittest.mock.patch("duediligence.deed_ai._build_model").

Uses LangChain's provider-agnostic chat-model interface specifically so the
backing model can be swapped (Anthropic today; OpenAI/Google later) without
touching the extraction schema or the calling code in services.py — the
schema and prompt are what actually encode the "never guess" contract, not
whichever provider happens to answer it.
"""

import json

from django.conf import settings
from pydantic import BaseModel, Field

ANTHROPIC_MODEL = "claude-sonnet-5"
OPENAI_MODEL = "gpt-4o"
GOOGLE_MODEL = "gemini-flash-latest"

# Repeated at both the schema level and per-field: every optional field is
# genuinely optional in the data, and a human reviews every extracted value
# before it's relied on (see services.extract_deed and the model's
# extraction_raw_response audit field) — but the model itself must not
# paper over gaps by inferring a plausible-looking value.
_NO_GUESSING_INSTRUCTION = (
    "Leave this null if it is not clearly and explicitly present in the "
    "document. Do not guess, infer, or estimate."
)


class DeedExtractionError(Exception):
    """Base class for anything that goes wrong extracting deed fields."""


class DeedExtractionConfigError(DeedExtractionError):
    """The configured provider's API key is not set, or AI_EXTRACTION_PROVIDER is unrecognized."""


class DeedExtractionRequestError(DeedExtractionError):
    """The API call itself failed (network, auth, rate limit, bad file)."""


class DeedExtractionParseError(DeedExtractionError):
    """The model's response didn't validate against the expected schema."""


class DeedExtractionSchema(BaseModel):
    """Structured fields extracted from a land registry document (sale
    deed, mutation extract, or similar). Every field below must reflect
    only what is explicitly printed in the document — never a guess, an
    inference from context, or a typical/expected value. If the document
    is not a land registry document at all, or is unreadable, leave every
    field null and explain why in confidence_notes."""

    document_type: str | None = Field(
        default=None,
        description="What kind of document this is, in your own words "
        "(e.g. 'registered sale deed', 'mutation extract'). " + _NO_GUESSING_INSTRUCTION,
    )
    seller_name: str | None = Field(
        default=None,
        description="Full name of the seller/transferor exactly as printed "
        "(including father's/husband's name and address if given as part "
        "of the same printed name block). " + _NO_GUESSING_INSTRUCTION,
    )
    buyer_name: str | None = Field(
        default=None,
        description="Full name of the buyer/transferee exactly as printed. "
        + _NO_GUESSING_INSTRUCTION,
    )
    deed_date: str | None = Field(
        default=None,
        description="Date the deed was executed/registered, as an ISO "
        "date string YYYY-MM-DD. " + _NO_GUESSING_INSTRUCTION,
    )
    consideration_amount: str | None = Field(
        default=None,
        description="The sale consideration amount, digits only, no "
        "commas or currency symbol. " + _NO_GUESSING_INSTRUCTION,
    )
    registration_number: str | None = Field(default=None, description=_NO_GUESSING_INSTRUCTION)
    book_number: str | None = Field(default=None, description="बही/Book number. " + _NO_GUESSING_INSTRUCTION)
    volume_number: str | None = Field(default=None, description="जिल्द/Volume number. " + _NO_GUESSING_INSTRUCTION)
    page_number: str | None = Field(default=None, description="पृष्ठ/Page number. " + _NO_GUESSING_INSTRUCTION)
    deed_area_value: str | None = Field(
        default=None,
        description="The TOTAL land area being conveyed, as a plain "
        "number (no units) — the grand-total figure for the whole "
        "transaction (often labelled कुल क्षेत्रफल/कुल रकबा), NOT the area "
        "of any single Khasra/Gata number even if that individual figure "
        "appears more prominently. If the deed lists per-khasra areas "
        "that sum to a stated total, use the stated total. "
        + _NO_GUESSING_INSTRUCTION,
    )
    deed_area_unit: str | None = Field(
        default=None,
        description="One of exactly: sqm, sqft, nali, bigha — matching "
        "the unit printed for deed_area_value. " + _NO_GUESSING_INSTRUCTION,
    )
    khasra_numbers: list[str] = Field(
        default_factory=list,
        description="Every Khasra/Gata number for the land being "
        "conveyed, exactly as printed (keep suffixes like '410' or "
        "'409/2'). A khasra number is a land-parcel identifier, always "
        "introduced by the words खसरा, खसरा नं0, or Khasra — do not "
        "include any other nearby number, in particular: fasli/agricultural "
        "year figures (e.g. फसली वर्ष 1417 से 1422 — 1417 and 1422 are "
        "YEARS, not khasra numbers), area/unit-conversion figures (e.g. "
        "'1014 वर्गफीट' or '112.66 वर्गगज' are AREA figures, not khasra "
        "numbers), khata numbers, or registration/case numbers. When in "
        "doubt whether a number is a khasra number, leave it out rather "
        "than include it. Empty list if none are printed.",
    )
    khata_number: str | None = Field(default=None, description=_NO_GUESSING_INSTRUCTION)
    village_name: str | None = Field(
        default=None,
        description="Village/mauza name exactly as printed, in its "
        "original script. This is a hint for a human to match against a "
        "government dropdown — it is never applied automatically. "
        + _NO_GUESSING_INSTRUCTION,
    )
    tehsil_name: str | None = Field(default=None, description="Same caveat as village_name. " + _NO_GUESSING_INSTRUCTION)
    district_name: str | None = Field(default=None, description="Same caveat as village_name. " + _NO_GUESSING_INSTRUCTION)
    confidence_notes: str = Field(
        default="",
        description="Free text: anything illegible, ambiguous, "
        "contradictory, or deliberately left blank, and why. Empty string "
        "if nothing to flag.",
    )


# Bounds worst-case latency well within gunicorn's --timeout 180 (see
# render.yaml) so a struggling provider fails fast with a clear error
# instead of hanging. Measured firsthand: without this, a live call to
# Google during a "high demand" 503 period took 11+ minutes to give up
# — the underlying SDK's own retry behavior, not our code — which
# would get silently killed by gunicorn with nothing saved anywhere.
REQUEST_TIMEOUT_SECONDS = 120


def _build_model():
    """Picks the backing chat model from settings.AI_EXTRACTION_PROVIDER.
    This is the one place that knows which provider is active — the schema,
    prompt, and calling code in services.py don't change either way."""
    provider = settings.AI_EXTRACTION_PROVIDER

    if provider == "anthropic":
        if not settings.ANTHROPIC_API_KEY:
            raise DeedExtractionConfigError(
                "ANTHROPIC_API_KEY is not configured — set it in the "
                "environment to enable AI deed extraction."
            )
        from langchain_anthropic import ChatAnthropic

        model = ChatAnthropic(
            model=ANTHROPIC_MODEL,
            api_key=settings.ANTHROPIC_API_KEY,
            default_request_timeout=REQUEST_TIMEOUT_SECONDS,
        )
    elif provider == "openai":
        if not settings.OPENAI_API_KEY:
            raise DeedExtractionConfigError(
                "OPENAI_API_KEY is not configured — set it in the "
                "environment to enable AI deed extraction."
            )
        from langchain_openai import ChatOpenAI

        model = ChatOpenAI(
            model=OPENAI_MODEL,
            api_key=settings.OPENAI_API_KEY,
            request_timeout=REQUEST_TIMEOUT_SECONDS,
        )
    elif provider == "google":
        if not settings.GOOGLE_API_KEY:
            raise DeedExtractionConfigError(
                "GOOGLE_API_KEY is not configured — set it in the "
                "environment to enable AI deed extraction."
            )
        from langchain_google_genai import ChatGoogleGenerativeAI

        model = ChatGoogleGenerativeAI(
            model=GOOGLE_MODEL,
            google_api_key=settings.GOOGLE_API_KEY,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    else:
        raise DeedExtractionConfigError(
            f"Unrecognized AI_EXTRACTION_PROVIDER: {provider!r} (expected "
            "'anthropic', 'openai', or 'google')."
        )

    return model.with_structured_output(DeedExtractionSchema, include_raw=True)


def extract_deed_fields(pdf_bytes: bytes) -> tuple[DeedExtractionSchema, str]:
    """Sends the PDF to the model and returns (validated result, raw JSON
    string of the extracted fields) — the raw JSON is what gets stored in
    TitleCheckReport.extraction_raw_response for audit."""
    import base64

    from langchain_core.messages import HumanMessage
    from langchain_core.messages.content import create_file_block

    model = _build_model()

    file_block = create_file_block(
        base64=base64.b64encode(pdf_bytes).decode("ascii"),
        mime_type="application/pdf",
        filename="deed.pdf",  # OpenAI's file_data format expects a filename
    )
    message = HumanMessage(
        content=[
            {
                "type": "text",
                "text": (
                    "Extract the structured fields defined by the schema from "
                    "this land registry document. Follow every field's "
                    "instruction to leave it null rather than guess."
                ),
            },
            file_block,
        ]
    )

    try:
        response = model.invoke([message])
    except DeedExtractionError:
        raise
    except Exception as exc:  # noqa: BLE001 — deliberately broad: wraps any
        # langchain/anthropic/httpx exception into our own hierarchy so
        # callers only need to catch DeedExtractionError.
        raise DeedExtractionRequestError(f"Deed extraction request failed: {exc}") from exc

    parsed = response.get("parsed") if isinstance(response, dict) else None
    if parsed is None:
        parsing_error = response.get("parsing_error") if isinstance(response, dict) else None
        raise DeedExtractionParseError(
            f"Model response did not match the expected schema: {parsing_error}"
        )

    return parsed, json.dumps(parsed.model_dump(), ensure_ascii=False, indent=2)
