"""One-off historical backfill into ``data/backfill_6m.csv``.

The daily file ``data/history.csv`` is never written. Each source is fetched on
its own. A source that has no history, no key, or a failed request leaves its
columns blank. Spot and the Hugging Face basket have no public history, so
those columns stay blank on purpose.

Notes on every row are exactly ``backfill6m``.
"""

import argparse
import logging
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from ai_demand.config import (
    AVERAGE_DECIMALS,
    HISTORY_COLUMNS,
    OPENROUTER_RANKINGS_URL,
    PRICE_DECIMALS,
    PYPI_OVERALL_URL,
    VAST_DISPLAY,
)
from ai_demand.errors import SourceError
from ai_demand.history import DEFAULT_CSV, format_int, format_number, write_rows
from ai_demand.http_client import get_json
from ai_demand.openrouter import daily_totals
from ai_demand.pjm import classify_overnight, fetch_pjm_history, seven_night_mean
from ai_demand.pypi_stats import mean_ending, parse_overall
from ai_demand.vast import fetch_public_payload, parse_daily

log = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")
UTC = ZoneInfo("UTC")

DEFAULT_START = date(2026, 5, 15)
BACKFILL_6M_CSV = Path("data/backfill_6m.csv")
NOTES = "backfill6m"
OPENROUTER_MAX_WINDOW_DAYS = 366

# Columns this backfill is allowed to fill. Everything else stays blank.
FILLED_COLUMNS = (
    "vllm_last_day",
    "vllm_7d_ma",
    "pjm_overnight_mw",
    "pjm_overnight_7d_ma",
    "or_total_tokens",
    "or_top5_share",
    "or_top1_model",
    "vast_h100_median",
    "vast_h200_median",
    "vast_b200_median",
)


def inclusive_dates(start: date, end: date) -> list[date]:
    """Every calendar date from ``start`` through ``end``, inclusive."""
    if end < start:
        raise ValueError(f"end {end.isoformat()} is before start {start.isoformat()}")
    days = []
    day = start
    while day <= end:
        days.append(day)
        day += timedelta(days=1)
    return days


def date_windows(start: date, end: date, size: int = OPENROUTER_MAX_WINDOW_DAYS) -> list[tuple[date, date]]:
    """Split ``[start, end]`` into inclusive windows of at most ``size`` days."""
    if size < 1:
        raise ValueError("window size must be positive")
    windows = []
    cursor = start
    while cursor <= end:
        chunk_end = min(end, cursor + timedelta(days=size - 1))
        windows.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return windows


def yesterday_kst(now: datetime | None = None) -> date:
    moment = now or datetime.now(KST)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=KST)
    return moment.astimezone(KST).date() - timedelta(days=1)


def build_backfill6m_rows(
    start: date,
    end: date,
    *,
    downloads: dict[date, int] | None = None,
    vast_daily: dict[str, dict[date, float]] | None = None,
    nights: dict[date, float] | None = None,
    openrouter_days: dict | None = None,
) -> list[dict[str, str]]:
    """One history-shaped row per date. Missing measurements stay blank."""
    downloads = downloads or {}
    vast_daily = vast_daily or {}
    nights = nights or {}
    openrouter_days = openrouter_days or {}
    rows = []
    for day in inclusive_dates(start, end):
        row = {column: "" for column in HISTORY_COLUMNS}
        row["date_kst"] = day.isoformat()
        row["notes"] = NOTES
        if day in downloads:
            row["vllm_last_day"] = format_int(downloads[day])
            row["vllm_7d_ma"] = format_number(mean_ending(downloads, day), AVERAGE_DECIMALS)
        for key, _display in VAST_DISPLAY:
            median = vast_daily.get(key, {}).get(day)
            if median is not None:
                row[f"vast_{key}_median"] = format_number(median, PRICE_DECIMALS)
        if day in nights:
            row["pjm_overnight_mw"] = format_number(nights[day], AVERAGE_DECIMALS)
            row["pjm_overnight_7d_ma"] = format_number(seven_night_mean(nights, day), AVERAGE_DECIMALS)
        ranking = openrouter_days.get(day)
        if ranking is not None:
            row["or_total_tokens"] = format_int(ranking.total_tokens)
            row["or_top5_share"] = format_number(ranking.top5_share, PRICE_DECIMALS)
            row["or_top1_model"] = ranking.top1_model
        rows.append(row)
    return rows


def _coverage(name: str, days: list[date]) -> str:
    if not days:
        return f"{name}: none"
    ordered = sorted(days)
    return f"{name}: {ordered[0].isoformat()}..{ordered[-1].isoformat()} ({len(ordered)} days)"


def fetch_openrouter_days(start: date, end: date, today_utc: date) -> dict:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise SourceError("missing_openrouter_key")
    merged: list = []
    for window_start, window_end in date_windows(start, end):
        try:
            payload = get_json(
                OPENROUTER_RANKINGS_URL,
                params={"start_date": window_start.isoformat(), "end_date": window_end.isoformat()},
                headers={"Authorization": f"Bearer {key}"},
            )
        except Exception as exc:
            log.warning("openrouter history request failed: %s", type(exc).__name__)
            raise SourceError("request_failed") from None
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            raise SourceError("not_in_response")
        merged.extend(data)
    days = daily_totals({"data": merged}, today_utc)
    return {item.day: item for item in days}


def _reason(exc: Exception) -> str:
    return getattr(exc, "code", None) or type(exc).__name__


def _load_sources(start: date, end: date, today_utc: date):
    downloads: dict[date, int] = {}
    vast_daily: dict[str, dict[date, float]] = {}
    nights: dict[date, float] = {}
    openrouter_days: dict = {}
    try:
        downloads = dict(parse_overall(get_json(PYPI_OVERALL_URL)))
        log.info(_coverage("pypi without_mirrors", list(downloads)))
    except Exception as exc:
        log.warning("pypi history unavailable: %s", _reason(exc))
    try:
        vast_daily = parse_daily(fetch_public_payload())
        for key, _display in VAST_DISPLAY:
            log.info(_coverage(f"vast {key} median", list(vast_daily.get(key, {}))))
    except Exception as exc:
        log.warning("vast history unavailable: %s", _reason(exc))
    try:
        points = fetch_pjm_history(start, end)
        nights, _incomplete = classify_overnight(points)
        log.info(_coverage("pjm overnight", list(nights)))
    except Exception as exc:
        log.warning("pjm history unavailable: %s", _reason(exc))
    try:
        openrouter_days = fetch_openrouter_days(start, end, today_utc)
        log.info(_coverage("openrouter", list(openrouter_days)))
    except Exception as exc:
        log.warning("openrouter history unavailable: %s", _reason(exc))
    return downloads, vast_daily, nights, openrouter_days


def backfill_6m(
    start: date = DEFAULT_START,
    end: date | None = None,
    path: Path = BACKFILL_6M_CSV,
    today_utc: date | None = None,
) -> int:
    """Write one row per date in ``[start, end]``. Returns 0 when any source worked."""
    if path.resolve() == DEFAULT_CSV.resolve():
        log.error("refusing to write %s", DEFAULT_CSV)
        return 1
    end = end or yesterday_kst()
    if end < start:
        log.error("end %s is before start %s", end.isoformat(), start.isoformat())
        return 1
    today = today_utc or datetime.now(UTC).date()
    downloads, vast_daily, nights, openrouter_days = _load_sources(start, end, today)
    if not any((downloads, any(vast_daily.values()), nights, openrouter_days)):
        log.error("every historical source failed; %s was not written", path)
        return 1
    rows = build_backfill6m_rows(
        start,
        end,
        downloads=downloads,
        vast_daily=vast_daily,
        nights=nights,
        openrouter_days=openrouter_days,
    )
    in_range = lambda days: [day for day in days if start <= day <= end]
    print(_coverage("pypi rows", in_range(list(downloads))))
    for key, _display in VAST_DISPLAY:
        print(_coverage(f"vast {key}", in_range(list(vast_daily.get(key, {})))))
    print(_coverage("pjm nights", in_range(list(nights))))
    print(_coverage("openrouter days", in_range(list(openrouter_days))))
    print(f"wrote {len(rows)} rows {start.isoformat()}..{end.isoformat()} -> {path}")
    try:
        write_rows(path, rows)
    except Exception as exc:
        log.error("backfill csv write failed: %s", type(exc).__name__)
        return 1
    return 0


def _parse_date(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"date must be YYYY-MM-DD ({exc})") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write data/backfill_6m.csv from public history.")
    parser.add_argument("--start", type=_parse_date, default=DEFAULT_START)
    parser.add_argument("--end", type=_parse_date, default=None, help="YYYY-MM-DD. Default: yesterday in Asia/Seoul.")
    parser.add_argument("--csv", type=Path, default=BACKFILL_6M_CSV)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return backfill_6m(start=args.start, end=args.end, path=args.csv)


if __name__ == "__main__":
    raise SystemExit(main())
