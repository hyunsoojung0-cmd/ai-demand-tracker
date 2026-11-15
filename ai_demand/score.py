"""Turn the morning's readings into one verdict.

Thresholds live at the top of ``ai_demand.config``. Unavailable signals are
left out of the sum. A present signal that sits inside its band contributes 0.

Spot is one signal. Its raw score adds +1 when the average bucket is at or
above ``SPOT_HIGH_BUCKET`` or the average rose versus the previous CSV row,
and -1 when the average is at or below ``SPOT_LOW_BUCKET`` or it fell. The
two directions cancel when both fire, and the result is clamped to +1, 0, or
-1 so Spot cannot outweigh the other indicators.

Vast contributes two signals (H100 and H200 medians). B200 is not scored.
vLLM compares last_day with the 7-day average. PJM compares the overnight
mean with the 7-night average. OpenRouter compares the completed day's total
tokens with its 7-day average. Hugging Face compares the implied daily basket
downloads with their 7-day average. Boundaries are strict: a change equal to the
threshold scores 0.
"""

from dataclasses import dataclass

from ai_demand.config import (
    HF_VS_MA_DOWN,
    HF_VS_MA_UP,
    OR_VS_MA_DOWN,
    OR_VS_MA_UP,
    PJM_VS_MA_DOWN,
    PJM_VS_MA_UP,
    SPOT_COLUMNS,
    SPOT_HIGH_BUCKET,
    SPOT_LOW_BUCKET,
    VAST_DOD_DOWN,
    VAST_DOD_UP,
    VAST_SCORED_KEYS,
    VERDICT_SOFTENING_AT,
    VERDICT_STRONG_AT,
    VLLM_VS_MA_DOWN,
    VLLM_VS_MA_UP,
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


def score_view(view: DayView, previous_spots: list[int] | None) -> Score:
    parts: list[Signal] = []
    spot = spot_signal(present_spots(view.spot_values), previous_spots)
    if spot is not None:
        parts.append(Signal("spot", spot))
    for key in VAST_SCORED_KEYS:
        quote = view.vast.get(key)
        current = quote.median if quote is not None else None
        signal = band_signal(current, view.vast_prev.get(key), VAST_DOD_UP, VAST_DOD_DOWN)
        if signal is not None:
            parts.append(Signal(f"vast_{key}", signal))
    vllm = band_signal(view.vllm_last_day, view.vllm_ma, VLLM_VS_MA_UP, VLLM_VS_MA_DOWN)
    if vllm is not None:
        parts.append(Signal("vllm", vllm))
    pjm = band_signal(view.pjm_mw, view.pjm_ma, PJM_VS_MA_UP, PJM_VS_MA_DOWN)
    if pjm is not None:
        parts.append(Signal("pjm", pjm))
    openrouter = band_signal(view.or_total, view.or_ma, OR_VS_MA_UP, OR_VS_MA_DOWN)
    if openrouter is not None:
        parts.append(Signal("openrouter", openrouter))
    huggingface = band_signal(view.hf_implied, view.hf_implied_ma, HF_VS_MA_UP, HF_VS_MA_DOWN)
    if huggingface is not None:
        parts.append(Signal("hf", huggingface))
    total = sum(part.value for part in parts)
    return Score(total=total, verdict=verdict_name(total), signals=tuple(parts))
