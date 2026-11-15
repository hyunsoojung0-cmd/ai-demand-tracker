"""Korean Telegram summary. Plain text, so labels such as ``<5%`` need no escaping."""

from ai_demand.config import SHEET_URL, SPOT_INSTANCES, VAST_DISPLAY
from ai_demand.model import DayView, VastQuote
from ai_demand.score import pct_change


def _missing(reason: str) -> str:
    return f"데이터 없음 ({reason})"


def _pct(frac: float) -> str:
    return f"{frac * 100:+.1f}%"


def _spot_lines(view: DayView) -> list[str]:
    lines = ["1. AWS Spot 중단률"]
    for target in SPOT_INSTANCES:
        bucket = view.spot_values.get(target.column)
        if bucket is None:
            shown = _missing(view.spot_reason)
        else:
            label = view.spot_labels.get(target.column, str(bucket))
            shown = f"{bucket + 1}단계 ({label})"
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


def render(view: DayView) -> str:
    lines = [f"📊 [AI Demand Daily] {view.date_kst}"]
    lines.extend(_spot_lines(view))
    lines.extend(_vast_lines(view))
    lines.extend(_vllm_lines(view))
    lines.extend(_pjm_lines(view))
    lines.append(f"종합: {view.overall}")
    lines.append(f"📈 시트: {SHEET_URL}")
    lines.append("Data: Vast.ai")
    return "\n".join(lines)
