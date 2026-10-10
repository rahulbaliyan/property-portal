"""Phase 7: the scoring engine. Computes a 0-100 score per ScoringFactor
category, an overall weighted score, and a separate confidence score
reflecting how much of that is backed by real automated data vs. a
neutral default for categories nothing has checked yet.

Per this system's own product principle: a score with low confidence is
not the same as a score with high confidence (spec example: "Score:
76/100, Confidence: LOW, Reason: Critical ownership verification is
pending."). Missing data never silently becomes a good OR bad score — it
becomes DEFAULT_CATEGORY_SCORE (neutral) with the gap reflected honestly
in confidence_score and PropertyScore.explanation instead.
"""

from decimal import Decimal

from django.utils import timezone

from .models import AnalysisRun, DueDiligenceCheck, PropertyScore, PropertyValuation, ScoringConfiguration, ScoringFactor

# 0-100 per DueDiligenceCheck.Status — PASS is clean, FAIL is a hard zero
# (a single real failure should hurt, not average away), WARNING sits
# between. PENDING_VERIFICATION/UNKNOWN deliberately have no entry here —
# they fall through to DEFAULT_CATEGORY_SCORE instead, since neither
# reflects an actual finding.
_STATUS_SCORE = {
    DueDiligenceCheck.Status.PASS: 100,
    DueDiligenceCheck.Status.WARNING: 60,
    DueDiligenceCheck.Status.FAIL: 0,
}

DEFAULT_CATEGORY_SCORE = 50  # neutral — "not yet known", never a guess at good or bad

# Which DueDiligenceCheck categories feed each ScoringFactor category.
# Deliberately lopsided toward LEGAL_DOCUMENTATION — that's genuinely
# where this project's only real automation (the duediligence/Bhulekh
# bridge) lives today. PRICE_VALUATION and MARKET have no entry here at
# all — they're driven by PropertyValuation (Phase 8) instead, which
# doesn't compute anything yet, so both stay at DEFAULT_CATEGORY_SCORE
# until it does.
_DUE_DILIGENCE_SOURCES = {
    ScoringFactor.Category.LEGAL_DOCUMENTATION: [
        DueDiligenceCheck.Category.OWNERSHIP,
        DueDiligenceCheck.Category.TITLE_DOCUMENTATION,
        DueDiligenceCheck.Category.LAND_CLASSIFICATION,
        DueDiligenceCheck.Category.LAND_USE,
        DueDiligenceCheck.Category.REGISTRY,
        DueDiligenceCheck.Category.ENCUMBRANCE,
        DueDiligenceCheck.Category.LITIGATION,
        DueDiligenceCheck.Category.NOC_APPROVAL,
        DueDiligenceCheck.Category.RERA_COMPLIANCE,
        DueDiligenceCheck.Category.DEVELOPER,
    ],
    ScoringFactor.Category.INFRASTRUCTURE: [
        DueDiligenceCheck.Category.ROAD_ACCESS,
        DueDiligenceCheck.Category.UTILITIES,
    ],
    ScoringFactor.Category.LOCATION: [DueDiligenceCheck.Category.LOCATION],
    ScoringFactor.Category.LIQUIDITY: [DueDiligenceCheck.Category.LIQUIDITY],
    ScoringFactor.Category.PROPERTY_QUALITY: [
        DueDiligenceCheck.Category.PROPERTY_CONDITION,
        DueDiligenceCheck.Category.ENVIRONMENTAL,
    ],
}


def _select_configuration(analysis_run: AnalysisRun) -> ScoringConfiguration | None:
    property_type = analysis_run.property_listing.property_type if analysis_run.property_listing else None
    qs = ScoringConfiguration.objects.filter(is_active=True)
    specific = qs.filter(property_type=property_type).first() if property_type else None
    return specific or qs.filter(property_type="").first()


def _category_score_from_checks(analysis_run, due_diligence_categories) -> tuple[int, bool]:
    """Returns (score_0_100, has_real_signal). has_real_signal is False
    when every relevant check is still PENDING_VERIFICATION/UNKNOWN/
    NOT_APPLICABLE, or none exist yet — the caller uses that both to pick
    DEFAULT_CATEGORY_SCORE and to count it against confidence."""
    checks = DueDiligenceCheck.objects.filter(
        analysis_run=analysis_run, category__in=due_diligence_categories
    ).exclude(status=DueDiligenceCheck.Status.NOT_APPLICABLE)
    scored = [_STATUS_SCORE[c.status] for c in checks if c.status in _STATUS_SCORE]
    if not scored:
        return DEFAULT_CATEGORY_SCORE, False
    return round(sum(scored) / len(scored)), True


def _price_valuation_score(analysis_run) -> tuple[int, bool]:
    try:
        valuation = analysis_run.valuation
    except PropertyValuation.DoesNotExist:
        return DEFAULT_CATEGORY_SCORE, False
    if valuation.premium_discount_percent is None:
        return DEFAULT_CATEGORY_SCORE, False
    # Close to the comparable median scores well; a large premium
    # (overpriced) or discount (possibly distressed) both pull it down —
    # premium more sharply, since a buyer directly pays for overpricing,
    # while a discount is often explicable and only mildly suspicious.
    pct = valuation.premium_discount_percent
    score = max(0, 100 - int(pct * 4)) if pct >= 0 else max(0, 100 - int(abs(pct) * 2))
    return min(score, 100), True


def _market_score(analysis_run) -> tuple[int, bool]:
    try:
        valuation = analysis_run.valuation
    except PropertyValuation.DoesNotExist:
        return DEFAULT_CATEGORY_SCORE, False
    if valuation.comparable_count <= 0:
        return DEFAULT_CATEGORY_SCORE, False
    # More comparables found nearby implies a more liquid, better-
    # understood market for this property type/area, independent of
    # whether the price itself is good.
    return min(100, 40 + valuation.comparable_count * 10), True


def _category_score(analysis_run, factor) -> tuple[int, bool]:
    if factor.category == ScoringFactor.Category.PRICE_VALUATION:
        return _price_valuation_score(analysis_run)
    if factor.category == ScoringFactor.Category.MARKET:
        return _market_score(analysis_run)
    return _category_score_from_checks(analysis_run, _DUE_DILIGENCE_SOURCES.get(factor.category, []))


def _build_explanation(category_scores, analysis_run) -> str:
    lines = [f"{ScoringFactor.Category(cat).label}: {score}/100" for cat, score in category_scores.items()]

    fail_categories = [
        c.get_category_display()
        for c in DueDiligenceCheck.objects.filter(analysis_run=analysis_run, status=DueDiligenceCheck.Status.FAIL)
    ]
    if fail_categories:
        lines.append(f"FAILED: {', '.join(fail_categories)} — see the due-diligence checklist.")

    unknown_count = DueDiligenceCheck.objects.filter(
        analysis_run=analysis_run,
        status__in=[DueDiligenceCheck.Status.UNKNOWN, DueDiligenceCheck.Status.PENDING_VERIFICATION],
    ).count()
    if unknown_count:
        noun = "category is" if unknown_count == 1 else "categories are"
        lines.append(f"{unknown_count} due-diligence {noun} still Unknown/Pending Verification — confidence is reduced accordingly.")

    return "\n".join(lines)


def compute_score(analysis_run: AnalysisRun) -> PropertyScore:
    """The entrypoint the admin's "Compute Score" button calls. Never
    raises — a missing ScoringConfiguration or input data degrades to a
    low-confidence neutral score, never an exception."""
    configuration = _select_configuration(analysis_run)
    factors = (
        list(configuration.factors.all())
        if configuration is not None
        else [ScoringFactor(category=c, weight_percent=Decimal("100") / 7) for c in ScoringFactor.Category.values]
    )

    category_scores: dict[str, int] = {}
    categories_with_signal = 0
    weighted_total = Decimal("0")
    weight_sum = Decimal("0")

    for factor in factors:
        score, has_signal = _category_score(analysis_run, factor)
        category_scores[factor.category] = score
        categories_with_signal += int(has_signal)
        weighted_total += Decimal(score) * factor.weight_percent
        weight_sum += factor.weight_percent

    overall_score = int(weighted_total / weight_sum) if weight_sum else None
    confidence_score = round(100 * categories_with_signal / len(factors)) if factors else 0
    missing_data_penalty_applied = categories_with_signal < len(factors)

    score, _created = PropertyScore.objects.update_or_create(
        analysis_run=analysis_run,
        defaults={
            "configuration": configuration,
            "category_scores": category_scores,
            "overall_score": overall_score,
            "confidence_score": confidence_score,
            "missing_data_penalty_applied": missing_data_penalty_applied,
            "explanation": _build_explanation(category_scores, analysis_run),
            "computed_at": timezone.now(),
        },
    )
    return score
