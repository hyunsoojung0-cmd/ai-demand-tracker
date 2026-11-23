"""In-memory shape of one collection day. Parsing, scoring, CSV, and the message share it."""

from dataclasses import dataclass, field
from datetime import date


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
    # Raw r for the previous CSV row and for the nearest row on or before
    # date-7. A missing key or None means that comparison is skipped.
    spot_prev: dict[str, int | None] = field(default_factory=dict)
    spot_week: dict[str, int | None] = field(default_factory=dict)
    or_total: int | None = None
    or_previous: int | None = None
    or_ma: float | None = None
    or_top5_share: float | None = None
    or_top1: str | None = None
    or_date: date | None = None
    or_reason: str = "요청 실패"
    or_weighted_price: float | None = None
    or_weighted_prev: float | None = None
    or_weighted_ma: float | None = None
    or_price_coverage: float | None = None
    or_est_spend_usd: float | None = None
    or_price_gpt: float | None = None
    or_price_claude: float | None = None
    or_price_gemini: float | None = None
    or_price_reason: str = "요청 실패"
    hf_30d: int | None = None
    hf_alltime: int | None = None
    hf_implied: int | None = None
    hf_implied_ma: float | None = None
    hf_failed: int = 0
    hf_basket_size: int = 0
    hf_reason: str = "요청 실패"
    vast_avail: dict[str, int | None] = field(default_factory=dict)
    vast_avail_prev: dict[str, int | None] = field(default_factory=dict)
    vast_avail_reason: str = "요청 실패"
    overall: str = ""
