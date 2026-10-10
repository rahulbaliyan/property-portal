"""Phase 3: AI-based extraction for any PropertyDocument (not just a sale
deed — duediligence/deed_ai.py already covers that one document type;
this covers the other fourteen: khasra, khatauni, mutation, NOC, RERA,
builder approval, site plan, etc.).

Deliberately mirrors deed_ai.py's shape (exception hierarchy, provider
selection, "leave null rather than guess" schema contract, raw-PDF-to-
multimodal-model approach instead of a separate OCR/text-extraction
library) rather than sharing code with it — that module is small, already
working, and tested; duplicating its ~40 lines of provider boilerplate
here is cheaper and safer than refactoring it mid-feature. A generic
schema is used across every document type instead of one schema per type,
since document_type varies a lot more than the facts worth extracting
from any of them (see Phase 4's field list) — avoids building and
maintaining fourteen near-identical schemas for a first version.

Pure I/O module — no Django model imports, fully mockable via
unittest.mock.patch("investment.document_ai._build_model").
"""

import json

from django.conf import settings
from pydantic import BaseModel, Field

ANTHROPIC_MODEL = "claude-sonnet-5"
OPENAI_MODEL = "gpt-4o"
GOOGLE_MODEL = "gemini-flash-latest"

_NO_GUESSING_INSTRUCTION = (
    "Leave this null/empty if it is not clearly and explicitly present in "
    "the document. Do not guess, infer, or estimate."
)

# Same reasoning as deed_ai.py's REQUEST_TIMEOUT_SECONDS: bounds worst-case
# latency within gunicorn's --timeout 180 (render.yaml) so a struggling
# provider fails fast with a clear error instead of hanging the worker.
REQUEST_TIMEOUT_SECONDS = 120


class DocumentExtractionError(Exception):
    """Base class for anything that goes wrong extracting document fields."""


class DocumentExtractionConfigError(DocumentExtractionError):
    """The configured provider's API key is not set, or AI_EXTRACTION_PROVIDER is unrecognized."""


class DocumentExtractionRequestError(DocumentExtractionError):
    """The API call itself failed (network, auth, rate limit, bad file)."""


class DocumentExtractionParseError(DocumentExtractionError):
    """The model's response didn't validate against the expected schema."""


class DocumentExtractionSchema(BaseModel):
    """Structured fields extracted from any property-related document
    (khasra, khatauni, mutation record, NOC, RERA filing, builder
    approval, site/layout plan, etc.) — never a guess, an inference from
    context, or a typical/expected value. If the document is unreadable
    or not actually a property-related document, leave every field null/
    empty and explain why in confidence_notes."""

    document_type_detected: str | None = Field(
        default=None,
        description="What kind of document this actually is, in your own words "
        "(e.g. 'khasra extract', 'NOC from forest department', 'builder "
        "approval letter') — compare this against the type the uploader "
        "selected; a mismatch is worth flagging. " + _NO_GUESSING_INSTRUCTION,
    )
    owner_or_seller_names: list[str] = Field(
        default_factory=list,
        description="Full name(s) of the current owner/seller/transferor exactly as "
        "printed. Empty list if none are printed.",
    )
    buyer_names: list[str] = Field(
        default_factory=list,
        description="Full name(s) of the buyer/transferee exactly as printed, if any. "
        "Empty list if none are printed.",
    )
    nominator_names: list[str] = Field(
        default_factory=list,
        description="Full name(s) of any 'Nominator' (or similarly-named third party "
        "distinct from seller and buyer who chose/confirmed the purchaser, e.g. under "
        "an agreement to sell) explicitly named as such in the document. Do not "
        "include someone here who is actually the seller, buyer, or a GPA/attorney "
        "acting on a seller's behalf. Empty list if the document has no such role.",
    )
    property_area: str | None = Field(
        default=None, description="The area figure as printed, digits only. " + _NO_GUESSING_INSTRUCTION
    )
    property_area_unit: str | None = Field(
        default=None,
        description="The unit printed alongside the area (e.g. sqm, sqft, gaj, nali, "
        "bigha, acre, hectare) exactly as it appears — do not convert or normalize.",
    )
    khasra_numbers: list[str] = Field(
        default_factory=list,
        description="Every Khasra/Gata number printed, exactly as printed (keep "
        "suffixes like '410' or '409/2'). Do not include fasli/agricultural year "
        "figures, area/unit-conversion figures, khata numbers, or case numbers.",
    )
    khata_number: str | None = Field(default=None, description=_NO_GUESSING_INSTRUCTION)
    survey_number: str | None = Field(
        default=None, description="Survey number, if the document uses that scheme instead of khasra. " + _NO_GUESSING_INSTRUCTION
    )
    property_address: str | None = Field(default=None, description="Printed property address. " + _NO_GUESSING_INSTRUCTION)
    land_classification: str | None = Field(
        default=None,
        description="Printed land classification/use (e.g. 'agricultural', "
        "'residential', 'commercial', 'nazul'). " + _NO_GUESSING_INSTRUCTION,
    )
    transaction_price: str | None = Field(
        default=None, description="Transaction/consideration amount, digits only, no commas or currency symbol. " + _NO_GUESSING_INSTRUCTION
    )
    document_dates: list[str] = Field(
        default_factory=list,
        description="Every meaningfully distinct date printed (execution, registration, "
        "approval, issue), as ISO date strings YYYY-MM-DD. Empty list if none are clearly dated.",
    )
    developer_or_project_name: str | None = Field(
        default=None, description="Builder/developer/project name, if this document references one. " + _NO_GUESSING_INSTRUCTION
    )
    mentioned_approvals: list[str] = Field(
        default_factory=list, description="Any government approvals/sanctions explicitly referenced by name or number."
    )
    mentioned_nocs: list[str] = Field(
        default_factory=list, description="Any No-Objection Certificates explicitly referenced by issuing authority or number."
    )
    restrictions: list[str] = Field(
        default_factory=list,
        description="Any explicitly stated restriction, encumbrance, condition, or "
        "limitation on the property (e.g. a mortgage, a lease, a court order, a "
        "mandated land use). Do not infer one from silence.",
    )
    inconsistencies_noted: list[str] = Field(
        default_factory=list,
        description="Anything internally contradictory within THIS document (e.g. two "
        "different areas stated for the same khasra, mismatched names in different "
        "places). Do not compare against other documents you haven't seen.",
    )
    missing_information: list[str] = Field(
        default_factory=list,
        description="Fields you would normally expect this type of document to carry "
        "but which are genuinely absent, illegible, or cut off.",
    )
    confidence_notes: str = Field(
        default="",
        description="Free text: anything illegible, ambiguous, or deliberately left "
        "blank, and why. Empty string if nothing to flag.",
    )


def _build_model():
    """Picks the backing chat model from settings.AI_EXTRACTION_PROVIDER —
    same provider switch duediligence.deed_ai and core.content_review
    already use, so this module needs no separate configuration."""
    provider = settings.AI_EXTRACTION_PROVIDER

    if provider == "anthropic":
        if not settings.ANTHROPIC_API_KEY:
            raise DocumentExtractionConfigError(
                "ANTHROPIC_API_KEY is not configured — set it in the "
                "environment to enable AI document extraction."
            )
        from langchain_anthropic import ChatAnthropic

        model = ChatAnthropic(
            model=ANTHROPIC_MODEL,
            api_key=settings.ANTHROPIC_API_KEY,
            default_request_timeout=REQUEST_TIMEOUT_SECONDS,
        )
    elif provider == "openai":
        if not settings.OPENAI_API_KEY:
            raise DocumentExtractionConfigError(
                "OPENAI_API_KEY is not configured — set it in the "
                "environment to enable AI document extraction."
            )
        from langchain_openai import ChatOpenAI

        model = ChatOpenAI(
            model=OPENAI_MODEL,
            api_key=settings.OPENAI_API_KEY,
            request_timeout=REQUEST_TIMEOUT_SECONDS,
        )
    elif provider == "google":
        if not settings.GOOGLE_API_KEY:
            raise DocumentExtractionConfigError(
                "GOOGLE_API_KEY is not configured — set it in the "
                "environment to enable AI document extraction."
            )
        from langchain_google_genai import ChatGoogleGenerativeAI

        model = ChatGoogleGenerativeAI(
            model=GOOGLE_MODEL,
            google_api_key=settings.GOOGLE_API_KEY,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    else:
        raise DocumentExtractionConfigError(
            f"Unrecognized AI_EXTRACTION_PROVIDER: {provider!r} (expected "
            "'anthropic', 'openai', or 'google')."
        )

    return model.with_structured_output(DocumentExtractionSchema, include_raw=True)


_MIME_TYPES = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}


def extract_document_fields(file_bytes: bytes, filename: str) -> tuple[DocumentExtractionSchema, str]:
    """Sends the file to the model and returns (validated result, raw JSON
    string of the extracted fields) — the raw JSON is what gets stored in
    DocumentAnalysis.raw_ai_response for audit. No separate OCR/text-
    extraction step: modern multimodal models read a PDF (scanned or
    native) or image directly, same approach deed_ai.py already uses in
    production."""
    import base64
    from pathlib import Path

    from langchain_core.messages import HumanMessage
    from langchain_core.messages.content import create_file_block

    model = _build_model()

    extension = Path(filename).suffix.lower()
    mime_type = _MIME_TYPES.get(extension, "application/pdf")
    file_block = create_file_block(
        base64=base64.b64encode(file_bytes).decode("ascii"),
        mime_type=mime_type,
        filename=filename,
    )
    message = HumanMessage(
        content=[
            {
                "type": "text",
                "text": (
                    "Extract the structured fields defined by the schema from this "
                    "property-related document. Follow every field's instruction to "
                    "leave it null/empty rather than guess."
                ),
            },
            file_block,
        ]
    )

    try:
        response = model.invoke([message])
    except DocumentExtractionError:
        raise
    except Exception as exc:  # noqa: BLE001 — deliberately broad: wraps any
        # langchain/provider-SDK exception into our own hierarchy so
        # callers only need to catch DocumentExtractionError.
        raise DocumentExtractionRequestError(f"Document extraction request failed: {exc}") from exc

    parsed = response.get("parsed") if isinstance(response, dict) else None
    if parsed is None:
        parsing_error = response.get("parsing_error") if isinstance(response, dict) else None
        raise DocumentExtractionParseError(
            f"Model response did not match the expected schema: {parsing_error}"
        )

    return parsed, json.dumps(parsed.model_dump(), ensure_ascii=False, indent=2)
