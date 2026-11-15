"""Korean Telegram summary. Plain text, so labels such as ``<5%`` need no escaping."""

from ai_demand.config import HF_BASKET, SHEET_URL, SPOT_INSTANCES, VAST_DISPLAY
from ai_demand.model import DayView, VastQuote
from ai_demand.score import pct_change


def _missing(reason: str) -> str:
    return f"데이터 없음 ({reason})"


def _pct(frac: float) -> str:
    return f"{frac * 100:+.1f}%"


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


def _spot_lines(view: DayView) -> list[str]:
    lines = ["1. AWS Spot 중단률"]
    for target in SPOT_INSTANCES:
        bucket = view.spot_values.get(target.column)
        if bucket is None:
            shown = _missing(view.spot_reason)
        else:
            label = view.spot_labels.get(target.column, str(bucket))
            history = spot_history_suffix(
                bucket,
                view.spot_prev.get(target.column),
                view.spot_week.get(target.column),
            )
            shown = f"{_stage(bucket)} ({label}){history}"
        lines.append(f"   - {target.display}: {shown}")
    return lines


def _vast_line(name: str, quote: VastQuote | None, previous: float | None, reason: str) -> str:
    quote = quote or VastQuote()
    if quote.median is None and quote.min_price is None:
        return f"   - {name}: {_missing(reason)}"
    if quote.median is None:
        median_bit = _missing(reason)
    else:
        change = pct_change(quote.median, previous)
        price = f"${quote.median:.2f}/hr"
        if change is None:
            median_bit = f"{price} (Δ 데이터 없음)"
        else:
            median_bit = f"{price} (Δ {_pct(change)})"
    if quote.min_price is None:
        low_bit = "최저 데이터 없음"
    else:
        low_bit = f"최저 ${quote.min_price:.2f}"
    return f"   - {name}: {median_bit} / {low_bit}"


def _vast_lines(view: DayView) -> list[str]:
    lines = ["2. Vast.ai 가격 (중간값 / 최저)"]
    for key, name in VAST_DISPLAY:
        lines.append(_vast_line(name, view.vast.get(key), view.vast_prev.get(key), view.vast_reason))
    return lines


def _vllm_lines(view: DayView) -> list[str]:
    lines = ["3. vLLM PyPI"]
    if view.vllm_last_day is None:
        lines.append(f"   - {_missing(view.vllm_reason)}")
        return lines
    shown = f"{view.vllm_last_day:,}"
    change = pct_change(view.vllm_last_day, view.vllm_ma)
    if change is None:
        lines.append(f"   - 전일 다운로드: {shown} (7일 평균 대비 데이터 없음)")
    else:
        lines.append(f"   - 전일 다운로드: {shown} (7일 평균 대비 {_pct(change)})")
    return lines


def _pjm_lines(view: DayView) -> list[str]:
    lines = ["4. PJM 야간 기저부하"]
    if view.pjm_mw is None:
        lines.append(f"   - {_missing(view.pjm_reason)}")
        return lines
    parts = []
    day_over_day = pct_change(view.pjm_mw, view.pjm_prev)
    versus_average = pct_change(view.pjm_mw, view.pjm_ma)
    if day_over_day is None:
        parts.append("전일 대비 데이터 없음")
    else:
        parts.append(f"전일 대비 {_pct(day_over_day)}")
    if versus_average is None:
        parts.append("7일 평균 대비 데이터 없음")
    else:
        parts.append(f"7일 평균 대비 {_pct(versus_average)}")
    lines.append(f"   - 야간 평균 {view.pjm_mw:,.0f} MW ({', '.join(parts)})")
    return lines


def _billions(tokens: int) -> str:
    return f"{tokens / 1_000_000_000:,.1f}B"


def _compact_count(value: int) -> str:
    magnitude = abs(value)
    if magnitude >= 1_000_000_000:
        return f"{value / 1_000_000_000:.1f}B"
    if magnitude >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    return f"{value:,}"


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


def _openrouter_lines(view: DayView) -> list[str]:
    lines = ["5. OpenRouter 토큰"]
    if view.or_total is None or view.or_date is None:
        lines.append(f"   - {_missing(view.or_reason)}")
        return lines
    label = f"{view.or_date.month}/{view.or_date.day}"
    lines.append(
        f"   - 전체 토큰({label} UTC): {_billions(view.or_total)} ({_comparison_bits(view.or_total, view.or_previous, view.or_ma)})"
    )
    if view.or_top1 and view.or_top5_share is not None:
        share = f"{view.or_top5_share * 100:.0f}%"
        lines.append(f"   - 1위 모델: {view.or_top1} (상위 5개 비중 {share})")
    return lines


def _huggingface_lines(view: DayView) -> list[str]:
    lines = ["6. Hugging Face 다운로드"]
    size = view.hf_basket_size or len(HF_BASKET)
    if view.hf_30d is None and view.hf_alltime is None and view.hf_implied is None:
        lines.append(f"   - {_missing(view.hf_reason)}")
        return lines
    if view.hf_failed:
        lines.append(f"   - {_missing('일부 모델 실패')}")
    elif view.hf_implied is None:
        lines.append("   - 누적 기준값 저장 (내일부터 일일치 표시)")
    else:
        change = pct_change(view.hf_implied, view.hf_implied_ma)
        daily = f"{view.hf_implied:,}"
        if change is None:
            lines.append(f"   - 대표 모델 {size}개 일일 다운로드: {daily}")
        else:
            lines.append(f"   - 대표 모델 {size}개 일일 다운로드: {daily} (7일 평균 대비 {_pct(change)})")
    if view.hf_30d is not None:
        suffix = f" ({size - view.hf_failed}/{size}개 합산)" if view.hf_failed else ""
        lines.append(f"   - 30일 누적: {_compact_count(view.hf_30d)}{suffix}")
    return lines


def render(view: DayView) -> str:
    lines = [f"📊 [AI Demand Daily] {view.date_kst}"]
    lines.extend(_spot_lines(view))
    lines.extend(_vast_lines(view))
    lines.extend(_vllm_lines(view))
    lines.extend(_pjm_lines(view))
    lines.extend(_openrouter_lines(view))
    lines.extend(_huggingface_lines(view))
    lines.append(f"종합: {view.overall}")
    lines.append(f"📈 시트: {SHEET_URL}")
    lines.append("Data: Vast.ai")
    lines.append("Source: OpenRouter (CC BY 4.0)")
    return "\n".join(lines)
