"""Phase 8: price & market analysis. Computes a PropertyValuation from
whatever PropertyComparable rows exist for a property — median/range
price-per-sq.-metre, and the asking price's premium or discount against
that range.

Comparables are admin-entered (a real verified transaction, or an
unverified listing), not auto-sourced: 99acres/MagicBricks/Housing.com/
NoBroker have no public API and their ToS explicitly restricts scraping
(confirmed by research this session) — real legal/IP-block risk at
scale, so this module deliberately doesn't attempt it. Uttarakhand's own
stamp-duty/circle-rate portal (registration.uk.gov.in) is a safer future
source for this engine to read from; it isn't wired in yet.

Only SQM/SQFT/ACRE are converted into a common basis (sq. metres) for
comparison — all three are exact, internationally standard units with no
regional variance. GAJ/NALI/BIGHA are deliberately left unconverted, same
principle duediligence.services._parse_area_sqm_for_khasra already
applies to hectares vs. those same units: a wrong silent conversion would
produce false confidence. A comparable in one of those units is simply
excluded from the price-per-unit statistics rather than guessed at.
"""

from decimal import Decimal, InvalidOperation

from django.utils import timezone

from properties.models import Property

from .models import AnalysisRun, PropertyValuation

# Exact, unambiguous conversions to sq. metres — international standard
# units, not regionally-defined ones.
_EXACT_SQM_FACTORS = {
    Property.AreaUnit.SQM: Decimal("1"),
    Property.AreaUnit.SQFT: Decimal("0.09290304"),
    Property.AreaUnit.ACRE: Decimal("4046.8564224"),
}


def _price_per_sqm(price, area_value, area_unit) -> Decimal | None:
    factor = _EXACT_SQM_FACTORS.get(area_unit)
    if factor is None or not price or not area_value:
        return None
    try:
        area_sqm = area_value * factor
        if area_sqm == 0:
            return None
        return (price / area_sqm).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def _comparable_price_per_sqm(comparable) -> Decimal | None:
    if comparable.price_per_unit is not None and comparable.area_unit == Property.AreaUnit.SQM:
        # Already per-sqm and stated as such — no conversion needed.
        return comparable.price_per_unit
    return _price_per_sqm(comparable.price, comparable.area_value, comparable.area_unit)


def compute_valuation(analysis_run: AnalysisRun) -> PropertyValuation:
    """The entrypoint the admin's "Compute Valuation" button calls. Never
    raises — no comparables, an asking price in an unconvertible unit, or
    any other gap just means a partial/empty PropertyValuation, never an
    exception."""
    comparables = list(analysis_run.comparables.all())
    per_sqm_prices = sorted(
        p for p in (_comparable_price_per_sqm(c) for c in comparables) if p is not None
    )

    median = lower = upper = None
    if per_sqm_prices:
        n = len(per_sqm_prices)
        mid = n // 2
        median = per_sqm_prices[mid] if n % 2 else (per_sqm_prices[mid - 1] + per_sqm_prices[mid]) / 2
        lower, upper = per_sqm_prices[0], per_sqm_prices[-1]

    property_listing = analysis_run.property_listing
    asking_price_per_sqm = None
    if property_listing is not None:
        asking_price_per_sqm = _price_per_sqm(
            property_listing.price, property_listing.area_value, property_listing.area_unit
        )

    premium_discount_percent = None
    if asking_price_per_sqm is not None and median:
        premium_discount_percent = ((asking_price_per_sqm - median) / median * 100).quantize(Decimal("0.01"))

    notes = []
    unconverted = [c for c in comparables if _comparable_price_per_sqm(c) is None]
    if unconverted:
        units = ", ".join(sorted({c.get_area_unit_display() for c in unconverted}))
        notes.append(
            f"{len(unconverted)} comparable(s) in {units} excluded from the price-per-sqm "
            "statistics — those units vary regionally and aren't auto-converted."
        )
    if any(not c.is_verified_transaction for c in comparables):
        notes.append("Includes unverified listing price(s), not confirmed transactions.")
    if not comparables:
        notes.append("No comparable properties recorded yet.")
    if asking_price_per_sqm is None and property_listing is not None:
        if not property_listing.price:
            notes.append("This property has no asking price set — no premium/discount computed.")
        elif property_listing.area_unit not in _EXACT_SQM_FACTORS:
            notes.append(
                f"This property's own area is in {property_listing.get_area_unit_display()}, "
                "which isn't auto-converted — no premium/discount computed."
            )

    valuation, _created = PropertyValuation.objects.update_or_create(
        analysis_run=analysis_run,
        defaults={
            "median_comparable_price_per_unit": median,
            "lower_range_price_per_unit": lower,
            "upper_range_price_per_unit": upper,
            "asking_price_per_unit": asking_price_per_sqm,
            "premium_discount_percent": premium_discount_percent,
            "comparable_count": len(per_sqm_prices),
            "methodology_notes": " ".join(notes),
            "calculated_at": timezone.now(),
        },
    )
    return valuation
