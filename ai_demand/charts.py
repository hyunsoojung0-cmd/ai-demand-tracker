"""Dashboard PNGs for the five core indicators.

The album follows the Telegram text: OpenRouter, Vast price, Vast
availability, AWS Spot, and PJM. Availability and Spot are omitted until two
days exist. vLLM and Hugging Face stay in the CSV and are not drawn.

A missing Korean font switches titles and subtitles to English. Gaps stay
gaps. ``publish_charts`` logs failures and does not raise, so a bad chart
cannot block the text message.
"""

import logging
import math
import os
import tempfile
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from ai_demand.commentary import merge_history, overlay_live
from ai_demand.config import SPOT_COLUMNS, SPOT_INSTANCES, VAST_DISPLAY, WINDOW_DAYS
from ai_demand.model import DayView

log = logging.getLogger(__name__)

CHART_DAYS = 90
MIN_SERIES_DAYS = 2
CHART_DIR_ENV = "CHART_DIR"

_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
)

_BLUE = "#2563EB"
_VIOLET = "#7C3AED"
_GREEN = "#059669"
_AMBER = "#D97706"
_INK = "#111827"
_MUTED = "#6B7280"
_GRID = "#E5E7EB"
_VAST_COLORS = {"h100": _BLUE, "h200": _VIOLET, "b200": _GREEN}
_SPOT_COLORS = (_BLUE, _VIOLET, _GREEN, _AMBER)


@dataclass(frozen=True)
class ChartFile:
    path: Path
    caption: str


def chart_dir() -> Path:
    return Path(os.environ.get(CHART_DIR_ENV, "charts"))


def korean_font_ready() -> bool:
    """Register NanumGothic when the file is installed. False means English labels."""
    import matplotlib
    from matplotlib import font_manager

    matplotlib.use("Agg", force=False)
    for candidate in _FONT_CANDIDATES:
        path = Path(candidate)
        if not path.is_file():
            continue
        font_manager.fontManager.addfont(str(path))
        name = font_manager.FontProperties(fname=str(path)).get_name()
        matplotlib.rcParams["font.family"] = name
        matplotlib.rcParams["axes.unicode_minus"] = False
        return True
    matplotlib.rcParams["axes.unicode_minus"] = False
    return False


def aligned_values(
    merged: dict[date, dict[str, str]],
    end: date,
    column: str,
    days: int = CHART_DAYS,
    lookback: int = 0,
) -> tuple[list[date], list[float | None]]:
    """Calendar days ending on ``end``. Missing cells are None so lines break."""
    total = days + lookback
    start = end - timedelta(days=total - 1)
    dates = [start + timedelta(days=offset) for offset in range(total)]
    values: list[float | None] = []
    for day in dates:
        row = merged.get(day) or {}
        raw = str(row.get(column) or "").strip()
        if not raw:
            values.append(None)
            continue
        try:
            values.append(float(raw))
        except ValueError:
            values.append(None)
    return dates, values


def publish_charts(
    *,
    daily_rows: list[dict[str, str]],
    backfill_rows: list[dict[str, str]],
    view: DayView,
    dry_run: bool,
) -> None:
    """Save PNGs on a dry run, or send them after the text. Never raises."""
    try:
        end = date.fromisoformat(view.date_kst)
        merged = overlay_live(merge_history(daily_rows, backfill_rows), view)
        if dry_run:
            saved = render_charts(merged, chart_dir(), end)
            log.info("dry_run: saved %s chart(s) to %s", len(saved), chart_dir())
            return
        token = os.environ.get("BOT_TOKEN", "").strip()
        chat_id = os.environ.get("CHAT_ID", "").strip()
        if not token or not chat_id:
            log.info("charts skipped (missing BOT_TOKEN/CHAT_ID)")
            return
        from ai_demand.telegram import send_album

        with tempfile.TemporaryDirectory(prefix="ai-demand-charts-") as tmp:
            saved = render_charts(merged, Path(tmp), end)
            if not saved:
                log.info("no charts to send")
                return
            send_album([(item.path, item.caption) for item in saved], token, chat_id)
            log.info("charts sent (%s)", len(saved))
    except Exception as exc:
        log.warning("charts failed: %s", type(exc).__name__)


def render_charts(
    merged: dict[date, dict[str, str]],
    dest: Path,
    end: date,
    *,
    days: int = CHART_DAYS,
    korean: bool | None = None,
) -> list[ChartFile]:
    """Write the dashboard PNGs that have enough data. Order matches the message."""
    dest.mkdir(parents=True, exist_ok=True)
    for stale in dest.glob("*.png"):
        stale.unlink()
    use_korean = korean_font_ready() if korean is None else korean
    made: list[ChartFile] = []
    builders = (
        _openrouter_chart,
        _vast_price_chart,
        _availability_chart,
        _spot_chart,
        _pjm_chart,
        _or_price_chart,
    )
    for builder in builders:
        item = builder(merged, dest, end, days, use_korean)
        if item is not None:
            made.append(item)
    return made


def _count_days(groups: list[list[float | None]]) -> int:
    if not groups:
        return 0
    return sum(any(value is not None for value in column) for column in zip(*groups))


def _trailing_ma(values: list[float | None], window: int = WINDOW_DAYS) -> list[float | None]:
    found: list[float | None] = []
    for index in range(len(values)):
        chunk = values[index - window + 1 : index + 1]
        if len(chunk) < window or any(item is None for item in chunk):
            found.append(None)
        else:
            found.append(sum(chunk) / window)
    return found


def _openrouter_chart(merged, dest, end, days, korean) -> ChartFile | None:
    full_dates, full_tokens = aligned_values(merged, end, "or_total_tokens", days, lookback=WINDOW_DAYS - 1)
    full_ma = _trailing_ma(full_tokens)
    offset = WINDOW_DAYS - 1
    dates = full_dates[offset:]
    tokens = [None if value is None else value / 1_000_000_000_000 for value in full_tokens[offset:]]
    average = [None if value is None else value / 1_000_000_000_000 for value in full_ma[offset:]]
    if not any(value is not None for value in tokens):
        log.info("openrouter chart skipped (no points)")
        return None
    title, subtitle = _copy(korean, "openrouter")
    daily, ma_label = ("일일", "7일 평균") if korean else ("daily", "7-day")
    return _draw(
        dest / "openrouter.png",
        title,
        subtitle,
        [
            (dates, tokens, daily, _BLUE, "-"),
            (dates, average, ma_label, _AMBER, "--"),
        ],
        lambda value: f"{value:.1f}T",
        caption=title,
    )


def _or_price_chart(merged, dest, end, days, korean) -> ChartFile | None:
    full_dates, full_prices = aligned_values(merged, end, "or_weighted_price", days, lookback=WINDOW_DAYS - 1)
    full_ma = _trailing_ma(full_prices)
    offset = WINDOW_DAYS - 1
    dates = full_dates[offset:]
    prices = full_prices[offset:]
    average = full_ma[offset:]
    points = sum(value is not None for value in prices)
    if points < MIN_SERIES_DAYS:
        log.info("openrouter price chart skipped (%s day(s))", points)
        return None
    title, subtitle = _copy(korean, "or_price")
    daily, ma_label = ("일일", "7일 평균") if korean else ("daily", "7-day")
    return _draw(
        dest / "or_price.png",
        title,
        subtitle,
        [
            (dates, prices, daily, _BLUE, "-"),
            (dates, average, ma_label, _AMBER, "--"),
        ],
        lambda value: f"${value:.2f}",
        caption=title,
    )


def _vast_price_chart(merged, dest, end, days, korean) -> ChartFile | None:
    series = []
    switch = _live_median_switch(merged, end, days)
    any_point = False
    for key, name in VAST_DISPLAY:
        dates, values = aligned_values(merged, end, f"vast_{key}_median", days)
        any_point = any_point or any(value is not None for value in values)
        series.append((dates, values, name, _VAST_COLORS[key], "-"))
    if not any_point:
        log.info("vast price chart skipped (no points)")
        return None
    title, subtitle = _copy(korean, "vast_price")
    return _draw(
        dest / "vast_price.png",
        title,
        subtitle,
        series,
        lambda value: f"${value:.2f}",
        caption=title,
        price_labels=True,
        marker=switch,
        marker_text="실시간" if korean else "live",
    )


def _availability_chart(merged, dest, end, days, korean) -> ChartFile | None:
    groups = []
    series = []
    for key, name in VAST_DISPLAY:
        dates, values = aligned_values(merged, end, f"vast_{key}_avail", days)
        groups.append(values)
        series.append((dates, values, name, _VAST_COLORS[key], "-"))
    count = _count_days(groups)
    if count < MIN_SERIES_DAYS:
        log.info("availability chart skipped (%s day(s))", count)
        return None
    title, subtitle = _copy(korean, "vast_avail")
    return _draw(
        dest / "vast_avail.png",
        title,
        subtitle,
        series,
        (lambda value: f"{value:.0f}대") if korean else (lambda value: f"{value:.0f}"),
        caption=title,
        integer_axis=True,
    )


def _spot_chart(merged, dest, end, days, korean) -> ChartFile | None:
    groups = []
    series = []
    for target, color in zip(SPOT_INSTANCES, _SPOT_COLORS):
        dates, raw = aligned_values(merged, end, target.column, days)
        stages = [None if value is None else value + 1 for value in raw]
        groups.append(stages)
        short = {
            "spot_use1_p5": "use1 p5",
            "spot_use1_p4d": "use1 p4d",
            "spot_usw2_p5": "usw2 p5",
            "spot_usw2_p4d": "usw2 p4d",
        }[target.column]
        series.append((dates, stages, short, color, "-"))
    count = _count_days(groups)
    if count < MIN_SERIES_DAYS:
        log.info("spot chart skipped (%s day(s))", count)
        return None
    title, subtitle = _copy(korean, "spot")
    return _draw(
        dest / "spot.png",
        title,
        subtitle,
        series,
        lambda value: f"{value:.0f}",
        caption=title,
        step=True,
        y_ticks=(1, 2, 3, 4, 5),
    )


def _pjm_chart(merged, dest, end, days, korean) -> ChartFile | None:
    dates, nightly = aligned_values(merged, end, "pjm_overnight_mw", days)
    _, average = aligned_values(merged, end, "pjm_overnight_7d_ma", days)
    if not any(value is not None for value in nightly):
        log.info("pjm chart skipped (no points)")
        return None
    title, subtitle = _copy(korean, "pjm")
    daily, ma_label = ("야간", "7일 평균") if korean else ("night", "7-day")
    return _draw(
        dest / "pjm.png",
        title,
        subtitle,
        [
            (dates, nightly, daily, _BLUE, "-"),
            (dates, average, ma_label, _AMBER, "--"),
        ],
        lambda value: f"{value:,.0f} MW",
        caption=title,
    )


def _copy(korean: bool, kind: str) -> tuple[str, str]:
    table = {
        "openrouter": (
            ("OpenRouter 일일 토큰", "조 토큰 (T) · 최근 90일"),
            ("OpenRouter daily tokens", "trillion tokens (T) · last 90 days"),
        ),
        "or_price": (
            ("OpenRouter 가중 가격", "USD/1M 토큰 · 입력 3 : 출력 1 · 최근 90일"),
            ("OpenRouter weighted price", "USD per 1M tokens · 3:1 prompt:completion · last 90 days"),
        ),
        "vast_price": (
            ("Vast.ai 중간 가격", "달러/시간 · 최근 90일 · 백필 일일 중간값에서 실시간 중간값으로 이어짐"),
            ("Vast.ai median price", "USD per hour · last 90 days · backfill daily median, then live median"),
        ),
        "vast_avail": (
            ("Vast.ai 대여 가능 머신", "대수 · 최근 90일 · 키 없는 공개 검색"),
            ("Vast.ai rentable machines", "machines · last 90 days · public search, no key"),
        ),
        "spot": (
            ("AWS Spot 중단률", "1–5단계 · 최근 90일"),
            ("AWS Spot interruption stage", "stages 1-5 · last 90 days"),
        ),
        "pjm": (
            ("PJM 야간 기저부하", "MW · 미동부 00:00–05:00 · 최근 90일"),
            ("PJM overnight baseload", "MW · US Eastern 00:00-05:00 · last 90 days"),
        ),
    }
    korean_copy, english_copy = table[kind]
    return korean_copy if korean else english_copy


def _live_median_switch(merged: dict[date, dict[str, str]], end: date, days: int) -> date | None:
    """First day in the window whose median is not a backfill row."""
    start = end - timedelta(days=days - 1)
    previous_backfill = False
    for offset in range(days):
        day = start + timedelta(days=offset)
        row = merged.get(day) or {}
        has_median = any(str(row.get(f"vast_{key}_median") or "").strip() for key, _name in VAST_DISPLAY)
        backfill = _is_backfill_row(str(row.get("notes") or ""))
        if has_median and not backfill and previous_backfill:
            return day
        if has_median and backfill:
            previous_backfill = True
    return None


def _is_backfill_row(notes: str) -> bool:
    parts = [part.strip().lower() for part in notes.split(";") if part.strip()]
    return bool(parts) and all(part in {"backfill", "backfill6m"} for part in parts)


def _draw(
    path: Path,
    title: str,
    subtitle: str,
    series: list[tuple[list[date], list[float | None], str, str, str]],
    formatter,
    *,
    caption: str,
    price_labels: bool = False,
    marker: date | None = None,
    marker_text: str = "",
    step: bool = False,
    integer_axis: bool = False,
    y_ticks: tuple[int, ...] | None = None,
) -> ChartFile:
    import matplotlib

    matplotlib.use("Agg", force=False)
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter, MaxNLocator
    from matplotlib.transforms import blended_transform_factory

    fig, ax = plt.subplots(figsize=(11.2, 5.5), dpi=140)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    ends = []
    for dates, values, name, color, style in series:
        ys = [float("nan") if value is None else value for value in values]
        if all(math.isnan(item) for item in ys):
            continue
        if step:
            ax.step(dates, ys, where="mid", color=color, linewidth=2.1, label=name, solid_capstyle="round")
        else:
            ax.plot(dates, ys, color=color, linewidth=2.2, linestyle=style, label=name, solid_capstyle="round")
        last = _last(dates, values)
        if last is not None:
            ax.scatter([last[0]], [last[1]], s=22, color=color, zorder=4)
            label = f"{name} ${last[1]:.2f}" if price_labels else f"{name} {formatter(last[1])}"
            ends.append((last[0], last[1], label, color))
    ax.set_xlim(series[0][0][0], series[0][0][-1])
    if y_ticks:
        ax.set_yticks(list(y_ticks))
        ax.set_ylim(0.5, 5.5)
    elif integer_axis:
        ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    _pad_ylim(ax)
    if not y_ticks and max(abs(bound) for bound in ax.get_ylim()) >= 1000:
        ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _pos: f"{value:,.0f}"))
    if marker is not None:
        ax.axvline(marker, color="#D1D5DB", linewidth=1, linestyle=":")
        if marker_text:
            ax.text(marker, ax.get_ylim()[1], marker_text, color=_MUTED, fontsize=8, ha="left", va="top")
    ax.grid(True, color=_GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.spines["left"].set_color(_GRID)
    ax.spines["bottom"].set_color(_GRID)
    ax.tick_params(colors=_MUTED, labelsize=9)
    ax.xaxis.set_major_locator(mdates.MonthLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
    legend = ax.legend(frameon=False, fontsize=9, loc="upper left")
    for text in legend.get_texts():
        text.set_color("#374151")
    fig.text(0.012, 0.98, title, fontsize=16, color=_INK, ha="left", va="top")
    fig.text(0.012, 0.915, subtitle, fontsize=10, color=_MUTED, ha="left", va="top")
    fig.subplots_adjust(left=0.08, right=0.70, top=0.82, bottom=0.12)
    _place_end_labels(ax, ends, blended_transform_factory(ax.transAxes, ax.transData))
    fig.savefig(path, facecolor="white")
    plt.close(fig)
    return ChartFile(path, caption)


def _last(dates: list[date], values: list[float | None]) -> tuple[date, float] | None:
    for day, value in zip(reversed(dates), reversed(values)):
        if value is not None:
            return day, value
    return None


def _pad_ylim(ax) -> None:
    lo, hi = ax.get_ylim()
    if y_is_fixed(lo, hi):
        return
    span = hi - lo
    pad = span * 0.14 if span else max(abs(hi) * 0.08, 1)
    ax.set_ylim(lo - pad, hi + pad)


def y_is_fixed(lo: float, hi: float) -> bool:
    return abs(lo - 0.5) < 1e-9 and abs(hi - 5.5) < 1e-9


def _place_end_labels(ax, items: list[tuple], transform) -> None:
    if not items:
        return
    lo, hi = ax.get_ylim()
    gap = max((hi - lo) * 0.07, 1e-6)
    order = sorted(range(len(items)), key=lambda index: items[index][1])
    placed = [items[index][1] for index in order]
    for index in range(1, len(placed)):
        if placed[index] - placed[index - 1] < gap:
            placed[index] = placed[index - 1] + gap
    if placed[-1] > hi:
        placed = [value - (placed[-1] - hi) for value in placed]
    if placed[0] < lo:
        placed = [value + (lo - placed[0]) for value in placed]
    for index, y_text in zip(order, placed):
        x_value, y_value, text, color = items[index]
        ax.annotate(
            text,
            xy=(x_value, y_value),
            xycoords="data",
            xytext=(1.02, y_text),
            textcoords=transform,
            va="center",
            ha="left",
            fontsize=9,
            color=color,
            annotation_clip=False,
        )
