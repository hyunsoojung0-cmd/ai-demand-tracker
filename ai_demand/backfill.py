"""Seed 2026-11-02..2026-11-14 from Vast daily medians and pypistats overall.

Spot, Vast minimums, PJM, and the verdict stay blank. ``notes`` is ``backfill``
when every seeded field is present. This does not send Telegram.
"""

import logging
from datetime import date, timedelta

from ai_demand.config import (
    AVERAGE_DECIMALS,
    BACKFILL_END,
    BACKFILL_START,
    HISTORY_COLUMNS,
    PRICE_DECIMALS,
    PYPI_OVERALL_URL,
    VAST_DISPLAY,
)
from ai_demand.history import DEFAULT_CSV, format_int, format_number, upsert_rows
from ai_demand.http_client import get_json
from ai_demand.pypi_stats import mean_ending, parse_overall
from ai_demand.vast import fetch_public_payload, parse_daily

log = logging.getLogger(__name__)


def build_backfill_row(
    day: date,
    medians: dict[str, float | None],
    downloads: dict[date, int],
) -> dict[str, str]:
    row = {column: "" for column in HISTORY_COLUMNS}
    row["date_kst"] = day.isoformat()
    notes = ["backfill"]
    for key, _display in VAST_DISPLAY:
        median = medians.get(key)
        if median is None:
            notes.append(f"vast {key} median missing")
        else:
            row[f"vast_{key}_median"] = format_number(median, PRICE_DECIMALS)
    if day not in downloads:
        notes.append("vllm day missing")
    else:
        row["vllm_last_day"] = format_int(downloads[day])
    average = mean_ending(downloads, day)
    if average is None:
        notes.append("vllm 7d ma missing")
    else:
        row["vllm_7d_ma"] = format_number(average, AVERAGE_DECIMALS)
    row["notes"] = "; ".join(notes)
    return row


def build_backfill_rows(
    daily: dict[str, dict[date, float]],
    series: list[tuple[date, int]],
    start: date = BACKFILL_START,
    end: date = BACKFILL_END,
) -> list[dict[str, str]]:
    downloads = dict(series)
    rows = []
    day = start
    while day <= end:
        medians = {key: daily.get(key, {}).get(day) for key, _display in VAST_DISPLAY}
        rows.append(build_backfill_row(day, medians, downloads))
        day += timedelta(days=1)
    return rows


def backfill(path=DEFAULT_CSV) -> int:
    try:
        payload = fetch_public_payload()
        daily = parse_daily(payload)
    except Exception as exc:
        log.error("backfill vast failed: %s", type(exc).__name__)
        return 1
    try:
        series = parse_overall(get_json(PYPI_OVERALL_URL))
    except Exception as exc:
        log.error("backfill pypi failed: %s", type(exc).__name__)
        return 1
    rows = build_backfill_rows(daily, series)
    try:
        upsert_rows(path, rows)
    except Exception as exc:
        log.error("backfill csv write failed: %s", type(exc).__name__)
        return 1
    log.info("backfill upserted %s rows into %s", len(rows), path)
    return 0
