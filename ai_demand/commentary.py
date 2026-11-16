"""Deterministic Korean commentary under the verdict.

No model calls. A missing source drops its sentence. History is the daily
file overlaid on the six-month backfill: the same date in ``history.csv``
replaces the backfill row.
"""

from datetime import date, timedelta

from ai_demand.config import SPOT_COLUMNS, WINDOW_DAYS
from ai_demand.model import DayView
from ai_demand.score import Score, pct_change

# Within this band a 30/90-day change is drawn as flat.
TREND_FLAT = 0.01
# OpenRouter one-day moves at least this large are marked noisy.
OR_DAY_SWING = 0.15
MOVE_LIMIT = 3
WINTER_MONTHS = (11, 12, 1, 2)
TELEGRAM_LIMIT = 4096

_PUSH = {
    ("spot", 1): "Spot 중단률 상승",
    ("spot", -1): "Spot 중단률 하락",
    ("vast_price", 1): "Vast 중간가 상승",
    ("vast_price", -1): "Vast 중간가 하락",
    ("vast_avail", 1): "Vast 대여 가능 대수 감소",
    ("vast_avail", -1): "Vast 대여 가능 대수 증가",
    ("pjm", 1): "PJM 야간부하 7일 평균 상회",
    ("pjm", -1): "PJM 야간부하 7일 평균 하회",
    ("openrouter", 1): "OpenRouter 토큰 7일 평균 상회",
    ("openrouter", -1): "OpenRouter 토큰 7일 평균 하회",
}
_PHRASE_ORDER = ("openrouter", "vast_price", "vast_avail", "spot", "pjm")
_AVAIL_COLUMNS = ("vast_h100_avail", "vast_h200_avail", "vast_b200_avail")


def _blank(value) -> bool:
    return str(value or "").strip() == ""


def merge_history(daily_rows: list[dict[str, str]], backfill_rows: list[dict[str, str]]) -> dict[date, dict[str, str]]:
    """Backfill first. A non-blank daily cell wins; a blank one does not erase history."""
    merged: dict[date, dict[str, str]] = {}
    for row in backfill_rows:
        key = _row_date(row)
        if key is not None:
            merged[key] = dict(row)
    for row in daily_rows:
        key = _row_date(row)
        if key is None:
            continue
        base = dict(merged.get(key, {}))
        for column, value in row.items():
            if column == "date_kst" or not _blank(value) or column not in base:
                base[column] = value
        base["date_kst"] = row["date_kst"]
        merged[key] = base
    return merged


def _row_date(row: dict[str, str]) -> date | None:
    try:
        return date.fromisoformat(str(row.get("date_kst") or ""))
    except ValueError:
        return None


def _put(row: dict[str, str], column: str, value) -> None:
    if value is None:
        return
    row[column] = str(value)


def overlay_live(history: dict[date, dict[str, str]], view: DayView) -> dict[date, dict[str, str]]:
    """Copy today's live readings onto the merged row without erasing other columns."""
    try:
        day = date.fromisoformat(view.date_kst)
    except ValueError:
        return history
    updated = dict(history)
    row = dict(updated.get(day, {}))
    row["date_kst"] = view.date_kst
    _put(row, "vllm_last_day", view.vllm_last_day)
    _put(row, "pjm_overnight_mw", view.pjm_mw)
    _put(row, "or_total_tokens", view.or_total)
    _put(row, "hf_implied_daily", view.hf_implied)
    for key, quote in view.vast.items():
        if quote is not None and quote.median is not None:
            _put(row, f"vast_{key}_median", quote.median)
    for key, count in (view.vast_avail or {}).items():
        _put(row, f"vast_{key}_avail", count)
    _put(row, "vllm_7d_ma", view.vllm_ma)
    _put(row, "pjm_overnight_7d_ma", view.pjm_ma)
    for column, bucket in view.spot_values.items():
        _put(row, column, bucket)
    updated[day] = row
    return updated


def _numeric_series(history: dict[date, dict[str, str]], column: str) -> dict[date, float]:
    series: dict[date, float] = {}
    for day, row in history.items():
        raw = str(row.get(column) or "").strip()
        if not raw:
            continue
        try:
            series[day] = float(raw)
        except ValueError:
            continue
    return series


def moving_average(series: dict[date, float], day: date, window: int = WINDOW_DAYS) -> float | None:
    """Mean of ``window`` contiguous days ending on ``day``, including ``day``."""
    values = []
    for offset in range(window - 1, -1, -1):
        key = day - timedelta(days=offset)
        if key not in series:
            return None
        values.append(series[key])
    return sum(values) / window


def _change_against(series: dict[date, float], today: date, lag: int) -> float | None:
    current = moving_average(series, today)
    baseline = moving_average(series, today - timedelta(days=lag))
    return pct_change(current, baseline)


def _arrow(frac: float) -> str:
    if frac > TREND_FLAT:
        return "↑"
    if frac < -TREND_FLAT:
        return "↓"
    return "→"


def _signed(frac: float) -> str:
    percent = frac * 100
    if abs(percent) < 0.05:
        percent = 0.0
    if abs(percent) >= 10:
        return f"{percent:+.0f}%"
    return f"{percent:+.1f}%"


def _tokens(value: float) -> str:
    magnitude = abs(value)
    if magnitude >= 1_000_000_000_000:
        return f"{value / 1_000_000_000_000:.1f}T"
    if magnitude >= 1_000_000_000:
        return f"{value / 1_000_000_000:.1f}B"
    return f"{value:,.0f}"


def _price(value: float) -> str:
    return f"${value:.2f}"


def _megawatts(value: float) -> str:
    return f"{value:,.0f} MW"


def _count(value: float) -> str:
    return f"{value:,.0f}"


def _signal_map(score: Score) -> dict[str, int]:
    return {part.name: part.value for part in score.signals}


def _phrases(signals: dict[str, int], direction: int) -> list[str]:
    phrases = []
    for name in _PHRASE_ORDER:
        if signals.get(name) == direction:
            phrases.append(_PUSH[(name, direction)])
    return phrases


def _basis_lines(score: Score) -> list[str]:
    signals = _signal_map(score)
    if not signals:
        return ["📝 판단 근거: 비교할 지표가 없습니다"]
    up = sum(value == 1 for value in signals.values())
    down = sum(value == -1 for value in signals.values())
    flat = sum(value == 0 for value in signals.values())
    lines = [f"📝 판단 근거: {len(signals)}개 지표 중 강세 {up} · 중립 {flat} · 약세 {down}"]
    if score.verdict == "Strong":
        pushed = _phrases(signals, 1)
    elif score.verdict == "Softening":
        pushed = _phrases(signals, -1)
    else:
        rose = _phrases(signals, 1)
        fell = _phrases(signals, -1)
        if rose and fell:
            lines.append("   강세: " + ", ".join(rose) + " / 약세: " + ", ".join(fell))
            return lines
        pushed = rose or fell
    if pushed:
        lines.append("   " + ", ".join(pushed))
    else:
        lines.append("   방향을 가른 신호가 없습니다")
    return lines


def _moves(view: DayView, history: dict[date, dict[str, str]], notes: str) -> list[str]:
    try:
        today = date.fromisoformat(view.date_kst)
    except ValueError:
        return []
    noisy_vast = "dod vs backfill" in notes
    found = []
    for key, label in (("h100", "Vast H100"), ("h200", "Vast H200"), ("b200", "Vast B200")):
        quote = view.vast.get(key)
        current = None if quote is None else quote.median
        _add_move(found, label, pct_change(current, view.vast_prev.get(key)), "백필 일별값과 비교" if noisy_vast else "")
    for key, label in (("h100", "Vast H100 가용"), ("h200", "Vast H200 가용"), ("b200", "Vast B200 가용")):
        _add_move(
            found,
            label,
            pct_change(view.vast_avail.get(key), view.vast_avail_prev.get(key)),
            "",
        )
    _add_move(found, "PJM", pct_change(view.pjm_mw, view.pjm_prev), "")
    or_note = ""
    or_change = pct_change(view.or_total, view.or_previous)
    if or_change is not None and abs(or_change) >= OR_DAY_SWING:
        or_note = "하루 변동이 큼"
    _add_move(found, "OpenRouter", or_change, or_note)
    found.sort(key=lambda item: (-abs(item[0]), item[1]))
    shown = [item[2] for item in found[:MOVE_LIMIT]]
    if not shown:
        return []
    return ["🔄 주요 변동: " + ", ".join(shown)]


def _add_move(found: list, label: str, frac: float | None, note: str) -> None:
    if frac is None:
        return
    text = f"{label} {_signed(frac)}"
    if note:
        text += f"({note})"
    found.append((frac, label, text))


def _trend_line(label: str, level: float, formatter, pct30: float | None, pct90: float | None) -> str | None:
    if pct30 is None and pct90 is None:
        return None
    bits = [f"{label} 7일평균 {formatter(level)}"]
    if pct30 is not None:
        bits.append(f"30일 전 대비 {_arrow(pct30)} {_signed(pct30)}")
    if pct90 is not None:
        bits.append(f"90일 전 대비 {_arrow(pct90)} {_signed(pct90)}")
    return "   " + ", ".join(bits)


def _trend_lines(view: DayView, history: dict[date, dict[str, str]]) -> list[str]:
    try:
        today = date.fromisoformat(view.date_kst)
    except ValueError:
        return []
    specs = (
        ("OpenRouter 토큰", "or_total_tokens", _tokens),
        ("PJM 야간", "pjm_overnight_mw", _megawatts),
        ("Vast H100 중간가", "vast_h100_median", _price),
    )
    body = []
    for label, column, formatter in specs:
        series = _numeric_series(history, column)
        level = moving_average(series, today)
        if level is None:
            continue
        line = _trend_line(
            label,
            level,
            formatter,
            _change_against(series, today, 30),
            _change_against(series, today, 90),
        )
        if line:
            body.append(line)
    avail = _availability_total(history)
    avail_level = moving_average(avail, today)
    if avail_level is not None:
        line = _trend_line(
            "Vast 가용",
            avail_level,
            _count,
            _change_against(avail, today, 30),
            _change_against(avail, today, 90),
        )
        if line:
            body.append(line)
    spot = _spot_trend(view)
    if spot:
        body.append("   " + spot)
    if not body:
        return []
    body[0] = "📈 추이: " + body[0].lstrip()
    return body


def _spot_trend(view: DayView) -> str | None:
    rose = fell = flat = 0
    for column in SPOT_COLUMNS:
        current = view.spot_values.get(column)
        earlier = view.spot_week.get(column)
        if current is None or earlier is None:
            continue
        if current > earlier:
            rose += 1
        elif current < earlier:
            fell += 1
        else:
            flat += 1
    if rose + fell + flat == 0:
        return None
    return f"Spot 중단률: 7일 전보다 상승 {rose} · 하락 {fell} · 보합 {flat}"


def _availability_total(history: dict[date, dict[str, str]]) -> dict[date, float]:
    """Sum of the three GPU counts. A day missing any GPU is a gap."""
    series: dict[date, float] = {}
    for day, row in history.items():
        values = []
        complete = True
        for column in _AVAIL_COLUMNS:
            raw = str(row.get(column) or "").strip()
            if not raw:
                complete = False
                break
            try:
                values.append(float(raw))
            except ValueError:
                complete = False
                break
        if complete:
            series[day] = sum(values)
    return series


def _sign(value: int | None, trend: float | None) -> int | None:
    if value in (-1, 1):
        return value
    if value == 0:
        return 0
    if trend is None:
        return None
    if trend > TREND_FLAT:
        return 1
    if trend < -TREND_FLAT:
        return -1
    return 0


def _price_sign(signals: dict[str, int], h100: float | None, h200: float | None, trust_score: bool) -> int | None:
    if trust_score and "vast_price" in signals:
        return signals["vast_price"]
    trends = [item for item in (h100, h200) if item is not None]
    if not trends:
        return None
    ups = sum(item > TREND_FLAT for item in trends)
    downs = sum(item < -TREND_FLAT for item in trends)
    if ups and not downs:
        return 1
    if downs and not ups:
        return -1
    if ups and downs:
        return 0
    return 0


def _interpretation(view: DayView, score: Score, history: dict[date, dict[str, str]], notes: str) -> str:
    signals = _signal_map(score)
    try:
        today = date.fromisoformat(view.date_kst)
        month = today.month
    except ValueError:
        today = None
        month = None
    or_trend = h100_trend = h200_trend = pjm_trend = None
    if today is not None:
        or_trend = _change_against(_numeric_series(history, "or_total_tokens"), today, 30)
        h100_trend = _change_against(_numeric_series(history, "vast_h100_median"), today, 30)
        h200_trend = _change_against(_numeric_series(history, "vast_h200_median"), today, 30)
        pjm_trend = _change_against(_numeric_series(history, "pjm_overnight_mw"), today, 30)
    usage = _sign(signals.get("openrouter"), or_trend)
    # A live-versus-backfill price jump is labeled noisy above, so the prose
    # follows the 30-day median trend instead of that day-over-day score.
    price = _price_sign(signals, h100_trend, h200_trend, trust_score="dod vs backfill" not in notes)
    avail = signals.get("vast_avail")
    sentences = []
    if price == 1 and avail == 1:
        sentences.append("가격은 오르고 대여 가능 대수는 줄어 공급이 빠듯하고 수요가 강한 모습입니다.")
    elif price == -1 and avail == -1:
        sentences.append("가격은 내리고 대여 가능 대수는 늘어 공급이 풀리는 모습입니다.")
    if usage is not None and price is not None and usage != 0 and price != 0:
        if usage > 0 and price > 0:
            sentences.append("사용량과 GPU 임대가격이 함께 오르고 있어 수요 우위 국면입니다.")
        elif usage > 0 and price < 0:
            sentences.append("사용량은 느는데 임대가격은 내려, 공급 확대가 수요 증가를 흡수하는 모습입니다.")
        elif usage < 0 and price < 0:
            sentences.append("사용량과 임대가격이 함께 내려 수요가 약해진 국면입니다.")
        else:
            sentences.append("사용량은 줄고 임대가격은 올라 공급이 빠듯한 모습입니다.")
    if _sign(signals.get("pjm"), pjm_trend) == 1 and month in WINTER_MONTHS:
        sentences.append("PJM 야간부하 상승에는 11–2월 난방 수요가 섞여 있을 수 있습니다.")
    if not sentences:
        sentences.append("뚜렷한 수급 신호가 없어 추이를 더 지켜봅니다.")
    return "💡 해설: " + " ".join(sentences[:2])


def build_commentary(
    view: DayView,
    score: Score,
    history: dict[date, dict[str, str]] | None = None,
    notes: str = "",
) -> list[str]:
    """Korean block placed immediately under the verdict. Never raises."""
    try:
        series = overlay_live(dict(history or {}), view)
        lines = _basis_lines(score)
        lines.extend(_moves(view, series, notes))
        lines.extend(_trend_lines(view, series))
        lines.append(_interpretation(view, score, series, notes))
        return lines
    except Exception:
        return ["💡 해설: 해설을 만들지 못했습니다."]


def fit_telegram(lines: list[str], limit: int = TELEGRAM_LIMIT) -> str:
    """Drop commentary from the bottom until the message fits."""
    text = "\n".join(lines)
    if len(text) <= limit:
        return text
    # Commentary starts at the verdict rationale and ends before the sheet link.
    start = next((i for i, line in enumerate(lines) if line.startswith("📝")), None)
    end = next((i for i, line in enumerate(lines) if line.startswith("📈 시트")), None)
    if start is None or end is None or start >= end:
        return text[:limit]
    kept = list(lines)
    while start < end and len("\n".join(kept)) > limit:
        end -= 1
        del kept[end]
    text = "\n".join(kept)
    if len(text) <= limit:
        return text
    return text[:limit]
