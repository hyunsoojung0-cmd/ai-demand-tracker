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
from dataclasses import dataclass
from datetime import date, datetime
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


def overnight_from_points(points) -> PJMReading:
    """Collapse hourly UTC points into complete Eastern overnight means."""
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
    if not complete:
        raise SourceError("incomplete")

    ordered = sorted(complete)
    latest = ordered[-1]
    previous = complete[ordered[-2]] if len(ordered) >= 2 else None
    ma = None
    if len(ordered) >= 7:
        last7 = ordered[-7:]
        ma = sum(complete[day] for day in last7) / 7
    latest_incomplete = bool(incomplete) and max(incomplete) > latest
    prior_gap = len(ordered) >= 2 and (latest - ordered[-2]).days != 1
    return PJMReading(
        overnight_mw=complete[latest],
        previous_mw=previous,
        ma_7d=ma,
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
