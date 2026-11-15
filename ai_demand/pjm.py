"""PJM overnight baseload from the EIA hourly region-data API.

``period`` is a UTC hour. It is converted to America/New_York. Overnight is
the mean of the hours whose local time starts at 00:00, 01:00, 02:00, 03:00,
and 04:00 — the half-open window [00:00, 05:00). A night counts only when all
five hours are present. The reported night is the newest complete one.

The 7-night average includes that night. The previous-night comparison uses
the complete night before it.
"""

import logging
import os
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from ai_demand.config import EIA_URL, PJM_HOURLY_LENGTH
from ai_demand.errors import SourceError
from ai_demand.http_client import get_json

log = logging.getLogger(__name__)

UTC = ZoneInfo("UTC")
EASTERN = ZoneInfo("America/New_York")
OVERNIGHT_HOURS = (0, 1, 2, 3, 4)


@dataclass(frozen=True)
class PJMReading:
    overnight_mw: float
    previous_mw: float | None
    ma_7d: float | None
    night: date
    latest_incomplete: bool
    prior_gap: bool


def parse_period(period: str) -> datetime:
    text = str(period).strip()
    if len(text) < 13:
        raise ValueError(period)
    parsed = datetime.strptime(text[:13], "%Y-%m-%dT%H")
    return parsed.replace(tzinfo=UTC)


def _as_float(value) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def iter_demand_points(payload: dict):
    """Yield ``(utc_period, mw)`` for PJM demand rows. Values may be strings."""
    if not isinstance(payload, dict):
        raise SourceError("not_in_response")
    body = payload.get("response") if isinstance(payload.get("response"), dict) else payload
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list):
        raise SourceError("not_in_response")
    for row in data:
        if not isinstance(row, dict):
            continue
        if row.get("respondent") not in (None, "PJM"):
            continue
        if row.get("type") not in (None, "D"):
            continue
        value = _as_float(row.get("value"))
        if value is None:
            continue
        try:
            period = parse_period(row["period"])
        except (KeyError, TypeError, ValueError):
            continue
        yield period, value


def classify_overnight(points) -> tuple[dict[date, float], list[date]]:
    """Mean MW for each complete Eastern night, plus dates that are missing an hour.

    A night is the half-open window [00:00, 05:00) America/New_York. Duplicate
    samples of the same local hour are averaged first, so each hour counts once.
    """
    buckets: dict[date, dict[int, list[float]]] = {}
    saw_any = False
    for period, value in points:
        saw_any = True
        local = period.astimezone(EASTERN)
        if local.hour not in OVERNIGHT_HOURS:
            continue
        buckets.setdefault(local.date(), {}).setdefault(local.hour, []).append(value)
    if not saw_any:
        raise SourceError("not_in_response")
    complete: dict[date, float] = {}
    incomplete: list[date] = []
    for day, hours in buckets.items():
        if any(hour not in hours or not hours[hour] for hour in OVERNIGHT_HOURS):
            incomplete.append(day)
            continue
        hourly = [sum(hours[hour]) / len(hours[hour]) for hour in OVERNIGHT_HOURS]
        complete[day] = sum(hourly) / len(hourly)
    return complete, incomplete


def seven_night_mean(nights: dict[date, float], day: date, window: int = 7) -> float | None:
    """Mean of the ``window`` most recent complete nights on or before ``day``.

    ``day`` itself must be complete. This is the same rule the daily run uses
    for the latest night.
    """
    if day not in nights:
        return None
    eligible = [item for item in sorted(nights) if item <= day]
    if len(eligible) < window:
        return None
    chosen = eligible[-window:]
    return sum(nights[item] for item in chosen) / window


def overnight_from_points(points) -> PJMReading:
    """Collapse hourly UTC points into the newest complete Eastern overnight mean."""
    complete, incomplete = classify_overnight(points)
    if not complete:
        raise SourceError("incomplete")
    ordered = sorted(complete)
    latest = ordered[-1]
    previous = complete[ordered[-2]] if len(ordered) >= 2 else None
    latest_incomplete = bool(incomplete) and max(incomplete) > latest
    prior_gap = len(ordered) >= 2 and (latest - ordered[-2]).days != 1
    return PJMReading(
        overnight_mw=complete[latest],
        previous_mw=previous,
        ma_7d=seven_night_mean(complete, latest),
        night=latest,
        latest_incomplete=latest_incomplete,
        prior_gap=prior_gap,
    )


def parse_pjm(payload: dict) -> PJMReading:
    return overnight_from_points(iter_demand_points(payload))


def _eia_url(api_key: str) -> str:
    params = [
        ("api_key", api_key),
        ("frequency", "hourly"),
        ("data[0]", "value"),
        ("facets[respondent][]", "PJM"),
        ("facets[type][]", "D"),
        ("sort[0][column]", "period"),
        ("sort[0][direction]", "desc"),
        ("length", str(PJM_HOURLY_LENGTH)),
    ]
    return EIA_URL + "?" + urlencode(params, safe="[]")


# The daily job asks for 240 recent hours and returns. A six-month call and
# even a 14-day call (runs 43716184862 and 43720072000) read-timeout. History
# therefore uses the same query shape as the daily job, over windows of at
# most a week. A window that still fails is skipped; earlier windows are kept.
EIA_HISTORY_CHUNK_DAYS = 7
EIA_HISTORY_TIMEOUT = (10, 90)
EIA_HISTORY_ATTEMPTS = 3
EIA_WINDOW_PAUSE_SECONDS = 1.0


def _raw_rows(payload: dict) -> tuple[list, int | None]:
    if not isinstance(payload, dict):
        raise SourceError("not_in_response")
    body = payload.get("response") if isinstance(payload.get("response"), dict) else payload
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list):
        raise SourceError("not_in_response")
    total_raw = body.get("total") if isinstance(body, dict) else None
    try:
        total = int(total_raw) if total_raw not in (None, "") else None
    except (TypeError, ValueError):
        total = None
    return data, total


def _eia_history_url(api_key: str, start: str, end: str, offset: int, length: int) -> str:
    """Same facets, frequency, data column, sort, and length as the daily job."""
    params = [
        ("api_key", api_key),
        ("frequency", "hourly"),
        ("data[0]", "value"),
        ("facets[respondent][]", "PJM"),
        ("facets[type][]", "D"),
        ("start", start),
        ("end", end),
        ("sort[0][column]", "period"),
        ("sort[0][direction]", "desc"),
        ("length", str(length)),
    ]
    if offset:
        params.append(("offset", str(offset)))
    return EIA_URL + "?" + urlencode(params, safe="[]")


def _format_hour(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H")


def _fetch_eia_window_once(
    api_key: str,
    start: datetime,
    end: datetime,
    page_length: int,
) -> list[tuple[datetime, float]]:
    """One weekly window. Pages with offset only when EIA fills ``page_length``."""
    points: list[tuple[datetime, float]] = []
    offset = 0
    while True:
        url = _eia_history_url(api_key, _format_hour(start), _format_hour(end), offset, page_length)
        payload = get_json(url, retries=1, timeout=EIA_HISTORY_TIMEOUT)
        raw, total = _raw_rows(payload)
        points.extend(iter_demand_points(payload))
        offset += len(raw)
        if not raw or len(raw) < page_length:
            break
        if total is not None and offset >= total:
            break
    return points


def _fetch_eia_window(
    api_key: str,
    start: datetime,
    end: datetime,
    page_length: int,
) -> list[tuple[datetime, float]]:
    """Retry one window with exponential backoff. The caller keeps other windows."""
    delay = 2.0
    last: Exception | None = None
    for attempt in range(1, EIA_HISTORY_ATTEMPTS + 1):
        try:
            return _fetch_eia_window_once(api_key, start, end, page_length)
        except Exception as exc:
            last = exc
            log.warning(
                "pjm window %s..%s attempt %s/%s failed: %s",
                _format_hour(start),
                _format_hour(end),
                attempt,
                EIA_HISTORY_ATTEMPTS,
                type(exc).__name__,
            )
            if attempt < EIA_HISTORY_ATTEMPTS:
                time.sleep(delay)
                delay *= 2
    assert last is not None
    raise last


def fetch_pjm_history(
    start: date,
    end: date,
    page_length: int = PJM_HOURLY_LENGTH,
    chunk_days: int = EIA_HISTORY_CHUNK_DAYS,
) -> list[tuple[datetime, float]]:
    """Hourly PJM demand from six nights before ``start`` through ``end``.

    Each request uses the daily job's length, sort, facets, and frequency,
    bounded by ``YYYY-MM-DDTHH``. A window that fails after retries is logged
    and skipped. The extra six nights let the first backfill rows form a
    7-night average when those hours exist.
    """
    key = os.environ.get("EIA_API_KEY", "").strip()
    if not key:
        raise SourceError("missing_key")
    cursor = datetime(start.year, start.month, start.day) - timedelta(days=6)
    last = datetime(end.year, end.month, end.day) + timedelta(days=1, hours=12)
    points: list[tuple[datetime, float]] = []
    skipped = 0
    first = True
    while cursor <= last:
        if not first and EIA_WINDOW_PAUSE_SECONDS:
            time.sleep(EIA_WINDOW_PAUSE_SECONDS)
        first = False
        chunk_end = min(last, cursor + timedelta(days=chunk_days) - timedelta(hours=1))
        try:
            points.extend(_fetch_eia_window(key, cursor, chunk_end, page_length))
        except Exception:
            skipped += 1
            log.warning("pjm window %s..%s skipped", _format_hour(cursor), _format_hour(chunk_end))
        cursor = chunk_end + timedelta(hours=1)
    if skipped:
        log.warning("pjm history skipped %s window(s)", skipped)
    if not points:
        raise SourceError("request_failed")
    return points


def fetch_pjm() -> PJMReading:
    key = os.environ.get("EIA_API_KEY", "").strip()
    if not key:
        raise SourceError("missing_key")
    try:
        payload = get_json(_eia_url(key))
    except Exception as exc:
        log.warning("pjm request failed: %s", type(exc).__name__)
        raise SourceError("request_failed") from None
    return parse_pjm(payload)
