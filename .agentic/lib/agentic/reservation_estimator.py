"""Generic cold-start planning assumptions, never measured project usage."""
from decimal import Decimal, InvalidOperation, ROUND_CEILING

from .budget_enrollment import require
from .token_limiter import MAX_TOKENS, digest

# Illustrative assumptions only: input (including cache) plus output per minute.
# Version changes are explicit; no project corpus, calibration or guarantee ships.
GENERIC_RATES_V1 = {'low': 100_000, 'medium': 250_000, 'high': 500_000}


def estimate_reservation(*, minutes, difficulty):
    require(type(minutes) in (int, float, Decimal), 'Estimate minutes must be numeric')
    try:
        duration = Decimal(str(minutes))
    except InvalidOperation as exc:
        raise ValueError('Estimate duration is invalid') from exc
    require(duration.is_finite() and duration > 0 and duration <= MAX_TOKENS,
            'Estimate duration must be positive and bounded')
    require(isinstance(difficulty, str) and difficulty in GENERIC_RATES_V1,
            'Estimate difficulty must be low, medium or high')
    rate = GENERIC_RATES_V1[difficulty]
    # Twofold planning margin and a positive context allowance, neither a bound.
    reservation = int((duration * rate * 2 + 100_000).to_integral_value(rounding=ROUND_CEILING))
    require(0 < reservation <= MAX_TOKENS, 'Rough reservation exceeds supported range')
    return dict(reservation_tokens=reservation, rough=True, estimator_version='generic-v1',
        rate_source='generic illustrative assumptions; no project measurements',
        rate_tokens_per_minute=rate, minutes=str(duration), difficulty=difficulty,
        rates_sha256=digest(GENERIC_RATES_V1), limits_enforced=False,
        cap_increase_authorized=False)
