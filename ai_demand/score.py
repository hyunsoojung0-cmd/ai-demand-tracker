"""Turn the morning's readings into one verdict.

Thresholds live at the top of ``ai_demand.config``. Unavailable signals are
left out of the sum. A present signal that sits inside its band contributes 0.

The verdict uses five indicators. vLLM, Hugging Face, AWS Spot, and the
OpenRouter weighted token price are collected for the sheet. Spot is not
scored. The weighted price is not scored. Estimated spend takes Spot's place:
same-day spend versus its 7-day average, with higher spend meaning demand.

Vast price is one signal: the mean of the H100 and H200 day-over-day median
changes. B200 is not scored. Vast availability is one signal on the sum of
rentable machines. A drop is +1 (tighter supply) and a rise is -1. PJM
compares the overnight mean with the 7-night average. OpenRouter compares
the completed day's total tokens with its 7-day average. Estimated spend
uses the same ±5% band against its own 7-day average. Boundaries are
strict: a change equal to the threshold scores 0.
"""

from dataclasses import dataclass

from ai_demand.config import (
    OR_SPEND_VS_MA_DOWN,
    OR_SPEND_VS_MA_UP,
    OR_VS_MA_DOWN,
    OR_VS_MA_UP,
    PJM_VS_MA_DOWN,
    PJM_VS_MA_UP,
    SPOT_COLUMNS,
    SPOT_HIGH_BUCKET,
    SPOT_LOW_BUCKET,
    VAST_AVAIL_DOWN,
    VAST_AVAIL_UP,
    VAST_DOD_DOWN,
    VAST_DOD_UP,
    VAST_SCORED_KEYS,
    VERDICT_SOFTENING_AT,
    VERDICT_STRONG_AT,
)
from ai_demand.model import DayView


@dataclass(frozen=True)
class Signal:
    name: str
    value: int


@dataclass(frozen=True)
class Score:
    total: int
    verdict: str
    signals: tuple[Signal, ...]


def pct_change(current: float | None, baseline: float | None) -> float | None:
    """Fractional change ``(current - baseline) / baseline``. None if it cannot be formed."""
    if current is None or baseline is None:
        return None
    if baseline == 0:
        return None
    return (float(current) - float(baseline)) / float(baseline)


def band_signal(current: float | None, baseline: float | None, up: float, down: float) -> int | None:
    frac = pct_change(current, baseline)
    if frac is None:
        return None
    if frac > up:
        return 1
    if frac < down:
        return -1
    return 0


def spot_signal(values: list[int] | None, previous: list[int] | None) -> int | None:
    if not values:
        return None
    average = sum(values) / len(values)
    raw = 0
    if average >= SPOT_HIGH_BUCKET:
        raw += 1
    if average <= SPOT_LOW_BUCKET:
        raw -= 1
    if previous:
        previous_average = sum(previous) / len(previous)
        if average > previous_average:
            raw += 1
        elif average < previous_average:
            raw -= 1
    if raw > 0:
        return 1
    if raw < 0:
        return -1
    return 0


def present_spots(values: dict[str, int | None]) -> list[int] | None:
    found = [int(values[column]) for column in SPOT_COLUMNS if values.get(column) is not None]
    return found or None


def verdict_name(total: int) -> str:
    if total >= VERDICT_STRONG_AT:
        return "Strong"
    if total <= VERDICT_SOFTENING_AT:
        return "Softening"
    return "Stable"


def vast_price_signal(view: DayView) -> int | None:
    """Mean of the H100 and H200 day-over-day changes, then the ±3% band."""
    fracs = []
    for key in VAST_SCORED_KEYS:
        quote = view.vast.get(key)
        current = None if quote is None else quote.median
        frac = pct_change(current, view.vast_prev.get(key))
        if frac is not None:
            fracs.append(frac)
    if not fracs:
        return None
    mean = sum(fracs) / len(fracs)
    return band_signal(1.0 + mean, 1.0, VAST_DOD_UP, VAST_DOD_DOWN)


def vast_avail_signal(current: dict[str, int | None] | None, previous: dict[str, int | None] | None) -> int | None:
    """Sum of GPUs that have both days. Fewer machines score +1."""
    cur = prev = 0.0
    paired = False
    for key in ("h100", "h200", "b200"):
        left = None if not current else current.get(key)
        right = None if not previous else previous.get(key)
        if left is None or right is None:
            continue
        cur += float(left)
        prev += float(right)
        paired = True
    if not paired:
        return None
    raw = band_signal(cur, prev, VAST_AVAIL_UP, VAST_AVAIL_DOWN)
    if raw is None:
        return None
    return -raw


def score_view(view: DayView, previous_spots: list[int] | None = None) -> Score:
    """Score the five verdict indicators. ``previous_spots`` is ignored.

    Spot stays on the sheet and is no longer a signal. Estimated spend
    replaces it. The weighted token price is not a signal.
    """
    del previous_spots
    parts: list[Signal] = []
    price = vast_price_signal(view)
    if price is not None:
        parts.append(Signal("vast_price", price))
    avail = vast_avail_signal(view.vast_avail, view.vast_avail_prev)
    if avail is not None:
        parts.append(Signal("vast_avail", avail))
    pjm = band_signal(view.pjm_mw, view.pjm_ma, PJM_VS_MA_UP, PJM_VS_MA_DOWN)
    if pjm is not None:
        parts.append(Signal("pjm", pjm))
    openrouter = band_signal(view.or_total, view.or_ma, OR_VS_MA_UP, OR_VS_MA_DOWN)
    if openrouter is not None:
        parts.append(Signal("openrouter", openrouter))
    spend = band_signal(view.or_est_spend_usd, view.or_spend_ma, OR_SPEND_VS_MA_UP, OR_SPEND_VS_MA_DOWN)
    if spend is not None:
        parts.append(Signal("or_spend", spend))
    total = sum(part.value for part in parts)
    return Score(total=total, verdict=verdict_name(total), signals=tuple(parts))
