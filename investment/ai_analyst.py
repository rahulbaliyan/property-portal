"""Phase 11: the AI property analyst. Synthesizes everything already
computed for an AnalysisRun (due-diligence checks, risks, evidence,
score) into a natural-language summary — it is never a source of new
facts. The prompt is explicit that every statement must trace back to
the structured data it's given; anything not present there must be
reported as missing, never guessed. It must never tell the reader to
"buy" or "don't buy" (this system's own product principle) — the final
decision stays with the human reading the report.

Mirrors document_ai.py/duediligence.deed_ai.py's provider-selection
pattern, but this is a summarization call over structured JSON text, not
a file-reading call — no file block, no multimodal/OCR concern.
"""

import json
import logging
import time

from django.conf import settings
from django.utils import timezone
from pydantic import BaseModel, Field

from .models import AnalysisRun

logger = logging.getLogger(__name__)

ANTHROPIC_MODEL = "claude-sonnet-5"
OPENAI_MODEL = "gpt-4o"
GOOGLE_MODEL = "gemini-flash-latest"
REQUEST_TIMEOUT_SECONDS = 60


class AiAnalystError(Exception):
    """Base class for anything that goes wrong generating the summary."""


class AiAnalystConfigError(AiAnalystError):
    """The configured provider's API key is not set, or AI_EXTRACTION_PROVIDER is unrecognized."""


class AiAnalystRequestError(AiAnalystError):
    """The API call itself failed (network, auth, rate limit)."""


class AiAnalystParseError(AiAnalystError):
    """The model's response didn't validate against the expected schema."""


class PropertyAnalysisSummary(BaseModel):
    executive_summary: str = Field(
        description="2-4 plain-language sentences summarizing the file. No new facts — "
        "only what the structured data already says."
    )
    key_positives: list[str] = Field(default_factory=list)
    key_risks: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(
        default_factory=list, description="What a thorough buyer would want but isn't in this data."
    )
    verification_checklist: list[str] = Field(default_factory=list)
    questions_for_seller: list[str] = Field(default_factory=list)


_SYSTEM_INSTRUCTION = (
    "You are summarizing a property due-diligence file for a prospective buyer. "
    "You are given ONLY structured data already computed by this system (due-"
    "diligence checks, risks, evidence, and a score) — you have not seen the "
    "original documents yourself. Every statement you make must be traceable to "
    "this data. Do not invent, infer, or assume ANY fact not explicitly present "
    "— no legal conclusions, no market data, no government approvals, no "
    "appreciation predictions, no claims about documents you weren't shown. "
    "Where information is genuinely missing or a category is unverified, say so "
    "plainly rather than guessing or glossing over it. This summary assists due "
    "diligence; it does not replace a lawyer, and you must NEVER tell the reader "
    "to 'buy' or 'don't buy' — only lay out what is known, unknown, and risky."
)


def _build_model():
    provider = settings.AI_EXTRACTION_PROVIDER

    if provider == "anthropic":
        if not settings.ANTHROPIC_API_KEY:
            raise AiAnalystConfigError(
                "ANTHROPIC_API_KEY is not configured — set it in the environment to enable the AI summary."
            )
        from langchain_anthropic import ChatAnthropic

        model = ChatAnthropic(
            model=ANTHROPIC_MODEL, api_key=settings.ANTHROPIC_API_KEY, default_request_timeout=REQUEST_TIMEOUT_SECONDS
        )
    elif provider == "openai":
        if not settings.OPENAI_API_KEY:
            raise AiAnalystConfigError(
                "OPENAI_API_KEY is not configured — set it in the environment to enable the AI summary."
            )
        from langchain_openai import ChatOpenAI

        model = ChatOpenAI(model=OPENAI_MODEL, api_key=settings.OPENAI_API_KEY, request_timeout=REQUEST_TIMEOUT_SECONDS)
    elif provider == "google":
        if not settings.GOOGLE_API_KEY:
            raise AiAnalystConfigError(
                "GOOGLE_API_KEY is not configured — set it in the environment to enable the AI summary."
            )
        from langchain_google_genai import ChatGoogleGenerativeAI

        model = ChatGoogleGenerativeAI(
            model=GOOGLE_MODEL, google_api_key=settings.GOOGLE_API_KEY, timeout=REQUEST_TIMEOUT_SECONDS
        )
    else:
        raise AiAnalystConfigError(
            f"Unrecognized AI_EXTRACTION_PROVIDER: {provider!r} (expected 'anthropic', 'openai', or 'google')."
        )

    return model.with_structured_output(PropertyAnalysisSummary, include_raw=True)


def build_context(analysis_run: AnalysisRun) -> dict:
    """Every field the AI analyst is allowed to know about — a plain dict
    so it's trivially inspectable/testable independent of the AI call
    itself."""
    property_listing = analysis_run.property_listing
    score = None
    try:
        s = analysis_run.score
        score = {
            "overall_score": s.overall_score,
            "confidence_score": s.confidence_score,
            "category_scores": s.category_scores,
            "missing_data_penalty_applied": s.missing_data_penalty_applied,
        }
    except Exception:  # noqa: BLE001 — PropertyScore.DoesNotExist, but also
        # defensively broad: this context-building must never itself crash
        # the summary generation over something as inessential as the score.
        pass

    return {
        "property": {
            "title": property_listing.title if property_listing else None,
            "property_type": property_listing.get_property_type_display() if property_listing else None,
            "region": property_listing.get_region_display() if property_listing else None,
        },
        "due_diligence_checks": [
            {"category": c.get_category_display(), "status": c.get_status_display(), "notes": c.notes}
            for c in analysis_run.due_diligence_checks.all()
        ],
        "risks": [
            {"category": r.get_category_display(), "severity": r.get_severity_display(), "reason": r.reason}
            for r in analysis_run.risks.all()
        ],
        "evidence": [
            {
                "claim": e.claim,
                "source_type": e.get_source_type_display(),
                "verification_status": e.get_verification_status_display(),
            }
            for e in analysis_run.evidence_items.all()
        ],
        "score": score,
    }


def generate_summary(analysis_run: AnalysisRun) -> None:
    """The entrypoint the admin's "Generate AI Summary" button calls.
    Never raises — a config/request/parse error is recorded on the run
    itself, mirroring duediligence.services/investment.services' existing
    contract for AI-calling entrypoints."""
    context_json = json.dumps(build_context(analysis_run), ensure_ascii=False, indent=2)

    try:
        model = _build_model()
    except AiAnalystConfigError as exc:
        analysis_run.ai_summary_error = str(exc)
        analysis_run.save(update_fields=["ai_summary_error"])
        return

    from langchain_core.messages import HumanMessage, SystemMessage

    started = time.monotonic()
    try:
        response = model.invoke(
            [
                SystemMessage(content=_SYSTEM_INSTRUCTION),
                HumanMessage(content=f"Structured due-diligence data:\n\n{context_json}"),
            ]
        )
    except Exception as exc:  # noqa: BLE001 — wraps any langchain/provider-SDK
        # exception; this function's contract is to never raise to the caller.
        logger.warning("AI summary request failed (run=%s, %.1fs): %s", analysis_run.pk, time.monotonic() - started, exc)
        analysis_run.ai_summary_error = f"AI summary request failed: {exc}"
        analysis_run.save(update_fields=["ai_summary_error"])
        return
    elapsed = time.monotonic() - started

    parsed = response.get("parsed") if isinstance(response, dict) else None
    if parsed is None:
        parsing_error = response.get("parsing_error") if isinstance(response, dict) else None
        logger.warning("AI summary schema mismatch (run=%s, %.1fs): %s", analysis_run.pk, elapsed, parsing_error)
        analysis_run.ai_summary_error = f"Model response did not match the expected schema: {parsing_error}"
        analysis_run.save(update_fields=["ai_summary_error"])
        return

    analysis_run.ai_summary = parsed.model_dump()
    analysis_run.ai_summary_raw_response = json.dumps(parsed.model_dump(), ensure_ascii=False, indent=2)
    analysis_run.ai_summary_error = ""
    analysis_run.ai_summary_generated_at = timezone.now()
    analysis_run.save(
        update_fields=["ai_summary", "ai_summary_raw_response", "ai_summary_error", "ai_summary_generated_at"]
    )
    logger.info("AI summary generated (run=%s, %.1fs)", analysis_run.pk, elapsed)
