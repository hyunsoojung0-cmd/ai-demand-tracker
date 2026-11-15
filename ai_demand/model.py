"""In-memory shape of one collection day. Parsing, scoring, CSV, and the message share it."""

from dataclasses import dataclass


@dataclass(frozen=True)
class VastQuote:
    min_price: float | None = None
    median: float | None = None


@dataclass(frozen=True)
class DayView:
    """One KST morning, after fetches and before the verdict is filled in.

    Missing measurements are ``None``. ``*_reason`` is the Korean parenthetical
    used when a whole source, or one of its fields, has nothing to show.
    """

    date_kst: str
    spot_values: dict[str, int | None]
    spot_labels: dict[str, str]
    spot_reason: str
    vast: dict[str, VastQuote]
    vast_prev: dict[str, float | None]
    vast_reason: str
    vllm_last_day: int | None
    vllm_ma: float | None
    vllm_reason: str
    pjm_mw: float | None
    pjm_prev: float | None
    pjm_ma: float | None
    pjm_reason: str
    overall: str = ""
