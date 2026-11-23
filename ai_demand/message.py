"""Korean Telegram summary. Plain text, so labels such as ``<5%`` need no escaping."""

from ai_demand.commentary import build_commentary, fit_telegram
from ai_demand.config import SHEET_URL, SPOT_COLUMNS, SPOT_INSTANCES, VAST_DISPLAY
from ai_demand.model import DayView, VastQuote
from ai_demand.score import Score, pct_change, score_view


def _missing(reason: str) -> str:
    return f"데이터 없음 ({reason})"


def _pct(frac: float) -> str:
    percent = frac * 100
    if abs(percent) < 0.05:
        percent = 0.0
    return f"{percent:+.1f}%"


def _stage(bucket: int) -> str:
    return f"{bucket + 1}단계"


def spot_history_suffix(current: int, prior: int | None, week: int | None) -> str:
    """Arrow versus the previous row, plus the earlier stages.

    Higher ``r`` is more interruption (↑), matching the Spot score. The
    parenthetical is omitted when neither comparison has a value. A blank
    7-day cell drops only the ``7일전`` clause.
    """
    if prior is None and week is None:
        return ""
    bits: list[str] = []
    arrow = ""
    if prior is not None:
        if current > prior:
            arrow = " ↑"
        elif current < prior:
            arrow = " ↓"
        else:
            arrow = " →"
        bits.append("전일 동일" if current == prior else f"전일 {_stage(prior)}")
    if week is not None:
        bits.append(f"7일전 {_stage(week)}")
    return f"{arrow} ({', '.join(bits)})"


def _spot_line(view: DayView) -> str:
    parts = []
    any_value = False
    for target in SPOT_INSTANCES:
        bucket = view.spot_values.get(target.column)
        if bucket is None:
            parts.append(f"{target.display}: {_missing(view.spot_reason)}")
            continue
        any_value = True
        label = view.spot_labels.get(target.column, str(bucket))
        history = spot_history_suffix(
            bucket,
            view.spot_prev.get(target.column),
            view.spot_week.get(target.column),
        )
        parts.append(f"{target.display}: {_stage(bucket)} ({label}){history}")
    if not any_value:
        return f"4. AWS Spot 중단률: {_missing(view.spot_reason)}"
    return "4. AWS Spot 중단률: " + " · ".join(parts)


def _price_bit(name: str, quote: VastQuote | None, previous: float | None, reason: str) -> str:
    quote = quote or VastQuote()
    if quote.median is None:
        return f"{name}: {_missing(reason)}"
    change = pct_change(quote.median, previous)
    price = f"${quote.median:.2f}"
    if change is None:
        return f"{name} {price}"
    return f"{name} {price} (Δ {_pct(change)})"


def _vast_price_line(view: DayView) -> str:
    parts = [_price_bit(name, view.vast.get(key), view.vast_prev.get(key), view.vast_reason) for key, name in VAST_DISPLAY]
    if all(part.endswith(")") and "데이터 없음" in part for part in parts):
        return f"2. Vast.ai 중간 가격: {_missing(view.vast_reason)}"
    return "2. Vast.ai 중간 가격: " + " · ".join(parts)


def _avail_bit(name: str, current: int | None, previous: int | None, reason: str) -> str:
    if current is None:
        return f"{name}: {_missing(reason)}"
    bit = f"{name} {int(current):,}대"
    change = pct_change(current, previous)
    if change is not None:
        bit += f" (Δ {_pct(change)})"
    return bit


def _avail_line(view: DayView) -> str:
    parts = [
        _avail_bit(name, view.vast_avail.get(key), view.vast_avail_prev.get(key), view.vast_avail_reason)
        for key, name in VAST_DISPLAY
    ]
    if all("데이터 없음" in part for part in parts):
        return f"3. Vast.ai 대여 가능: {_missing(view.vast_avail_reason)}"
    return "3. Vast.ai 대여 가능: " + " · ".join(parts)


def _pjm_line(view: DayView) -> str:
    if view.pjm_mw is None:
        return f"4. PJM 야간 기저부하: {_missing(view.pjm_reason)}"
    shown = f"{view.pjm_mw:,.0f} MW"
    return f"4. PJM 야간 기저부하: {shown} ({_comparison_bits(view.pjm_mw, view.pjm_prev, view.pjm_ma)})"


def _billions(tokens: int) -> str:
    return f"{tokens / 1_000_000_000:,.1f}B"


def _comparison_bits(current: float | None, previous: float | None, average: float | None) -> str:
    parts = []
    day_over_day = pct_change(current, previous)
    versus_average = pct_change(current, average)
    if day_over_day is None:
        parts.append("전일 대비 데이터 없음")
    else:
        parts.append(f"전일 대비 {_pct(day_over_day)}")
    if versus_average is None:
        parts.append("7일 평균 대비 데이터 없음")
    else:
        parts.append(f"7일 평균 대비 {_pct(versus_average)}")
    return ", ".join(parts)


def _openrouter_line(view: DayView) -> str:
    if view.or_total is None or view.or_date is None:
        return f"1. OpenRouter 일일 토큰: {_missing(view.or_reason)}"
    label = f"{view.or_date.month}/{view.or_date.day} UTC"
    compared = _comparison_bits(view.or_total, view.or_previous, view.or_ma)
    return f"1. OpenRouter 일일 토큰: {_billions(view.or_total)} ({label}, {compared})"


def _price_per_million(value: float) -> str:
    return f"${value:,.2f}/1M"


def _coverage(frac: float) -> str:
    return f"매칭 {frac * 100:.0f}%"


def _openrouter_price_line(view: DayView) -> str:
    if view.or_weighted_price is None or view.or_date is None:
        return f"5. OpenRouter 가중 가격: {_missing(view.or_price_reason)}"
    label = f"{view.or_date.month}/{view.or_date.day} UTC"
    compared = _comparison_bits(view.or_weighted_price, view.or_weighted_prev, view.or_weighted_ma)
    coverage = "" if view.or_price_coverage is None else f", {_coverage(view.or_price_coverage)}"
    return f"5. OpenRouter 가중 가격: {_price_per_million(view.or_weighted_price)} ({label}, {compared}{coverage})"


def _spend(value: float) -> str:
    return f"${value:,.0f}" if abs(value) >= 100 else f"${value:,.2f}"


def _spend_line(view: DayView) -> str:
    if view.or_est_spend_usd is None or view.or_date is None:
        return f"6. OpenRouter 추정 지출: {_missing(view.or_price_reason)}"
    label = f"{view.or_date.month}/{view.or_date.day} UTC"
    compared = _comparison_bits(view.or_est_spend_usd, view.or_spend_prev, view.or_spend_ma)
    return f"6. OpenRouter 추정 지출: {_spend(view.or_est_spend_usd)} ({label}, {compared})"


def _score_for(view: DayView, score: Score | None) -> Score:
    if score is not None:
        return score
    previous = [view.spot_prev[column] for column in SPOT_COLUMNS if view.spot_prev.get(column) is not None]
    return score_view(view, previous or None)


def render(
    view: DayView,
    score: Score | None = None,
    history: dict | None = None,
    notes: str = "",
) -> str:
    lines = [
        f"📊 [AI Demand Daily] {view.date_kst}",
        _openrouter_line(view),
        _vast_price_line(view),
        _avail_line(view),
        _pjm_line(view),
        _openrouter_price_line(view),
        _spend_line(view),
        f"종합: {view.overall}",
    ]
    lines.extend(build_commentary(view, _score_for(view, score), history, notes))
    lines.append(f"📈 시트: {SHEET_URL}")
    lines.append("Data: Vast.ai")
    lines.append("Source: OpenRouter (CC BY 4.0)")
    return fit_telegram(lines)
