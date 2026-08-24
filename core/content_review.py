"""AI-based content review for public form submissions — a secondary
quality layer on top of the honeypot, regex spam-content check, and
Turnstile CAPTCHA (see inquiries/forms.py and core/turnstile.py). Those
catch bots and structural spam (links, scripted fields); this catches
what they can't: well-formed spam with no links at all — scam pitches,
incoherent bot-generated text, abusive language.

Reuses the same AI_EXTRACTION_PROVIDER/API keys as duediligence's deed
extraction (see duediligence/deed_ai.py) rather than a separate set,
but stays independent of that app's code — this runs on every public
form submission, duediligence is an admin-only tool, and the two
shouldn't be coupled just because they both happen to call an LLM.
"""

import logging

from django.conf import settings
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Fast/cheap model per provider — this is a simple text classification
# on a couple of short fields, not the multi-page document understanding
# deed_ai.py needs, so it doesn't need that module's heavier models.
GOOGLE_MODEL = "gemini-flash-latest"
ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
OPENAI_MODEL = "gpt-4o-mini"

_REVIEW_INSTRUCTIONS = (
    "Review this property-website form submission. Flag it ONLY if it is "
    "clearly spam, a scam/phishing pitch, abusive or hateful language, or "
    "incoherent bot-generated text. Do NOT flag a submission just for "
    "being short, informal, or low on detail — a real customer asking "
    "'is this still available?' or similar is genuine. When in doubt, "
    "do not flag it."
)


class ContentReviewResult(BaseModel):
    is_spam_or_abuse: bool = Field(
        description="True only if this is clearly spam, a scam pitch, "
        "abusive/hateful language, or incoherent bot-generated text — "
        "never true just because a submission is brief or informal."
    )
    reason: str = Field(description="One short sentence explaining the classification.")


def is_content_review_configured() -> bool:
    if not settings.AI_CONTENT_REVIEW_ENABLED:
        return False
    provider = settings.AI_EXTRACTION_PROVIDER
    if provider == "google":
        return bool(settings.GOOGLE_API_KEY)
    if provider == "anthropic":
        return bool(settings.ANTHROPIC_API_KEY)
    if provider == "openai":
        return bool(settings.OPENAI_API_KEY)
    return False


def _build_model():
    provider = settings.AI_EXTRACTION_PROVIDER

    if provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI

        model = ChatGoogleGenerativeAI(model=GOOGLE_MODEL, google_api_key=settings.GOOGLE_API_KEY)
    elif provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        model = ChatAnthropic(model=ANTHROPIC_MODEL, api_key=settings.ANTHROPIC_API_KEY)
    elif provider == "openai":
        from langchain_openai import ChatOpenAI

        model = ChatOpenAI(model=OPENAI_MODEL, api_key=settings.OPENAI_API_KEY)
    else:
        return None

    return model.with_structured_output(ContentReviewResult, include_raw=True)


def review_submission(fields: dict) -> bool:
    """True if the submission should proceed. Fails OPEN — unconfigured,
    a request error, a timeout, or an unparseable response all return
    True — since this sits on top of checks that already gate bot
    traffic (Turnstile especially), an AI provider hiccup should never
    be what blocks a genuine customer."""
    if not is_content_review_configured():
        return True

    model = _build_model()
    if model is None:
        return True

    from langchain_core.messages import HumanMessage

    text = "\n".join(f"{key}: {value}" for key, value in fields.items() if value)
    message = HumanMessage(content=f"{_REVIEW_INSTRUCTIONS}\n\n{text}")

    try:
        response = model.invoke([message])
        parsed = response.get("parsed") if isinstance(response, dict) else None
        if parsed is None:
            return True
        if parsed.is_spam_or_abuse:
            logger.info("AI content review flagged a submission: %s", parsed.reason)
        return not parsed.is_spam_or_abuse
    except Exception:  # noqa: BLE001 — deliberately broad: any failure here fails open
        logger.exception("AI content review request failed — allowing submission through")
        return True
