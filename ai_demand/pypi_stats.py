"""vLLM download counts from pypistats.

``last_day`` comes from ``/recent``. The 7-day average comes from ``/overall``
rows whose category is ``without_mirrors``. The series lags about one day.
"""

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from ai_demand.config import PYPI_OVERALL_URL, PYPI_RECENT_URL, WINDOW_DAYS
from ai_demand.errors import SourceError
from ai_demand.http_client import get_json

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PyPIReading:
    last_day: int
    ma_7d: float | None
    latest_overall: int | None


def parse_recent(payload: dict) -> int:
    try:
        return int(payload["data"]["last_day"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SourceError("not_in_response") from exc


def parse_overall(payload: dict) -> list[tuple[date, int]]:
    """Daily ``without_mirrors`` downloads, oldest first. Mirror rows are dropped."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise SourceError("not_in_response")
    by_date: dict[date, int] = {}
    for row in data:
        if not isinstance(row, dict) or row.get("category") != "without_mirrors":
            continue
        try:
            day = date.fromisoformat(str(row["date"]))
            downloads = int(row["downloads"])
        except (KeyError, TypeError, ValueError):
            continue
        by_date[day] = downloads
    if not by_date:
        raise SourceError("not_in_response")
    return sorted(by_date.items())


def trailing_mean(series: list[tuple[date, int]], window: int = WINDOW_DAYS) -> float | None:
    """Mean of the latest ``window`` calendar days.

    The window must be contiguous. A hole in the series makes the average
    unavailable instead of silently spanning a longer stretch.
    """
    ordered = sorted(series)
    if len(ordered) < window:
        return None
    tail = ordered[-window:]
    if (tail[-1][0] - tail[0][0]).days != window - 1:
        return None
    return sum(downloads for _, downloads in tail) / window


def mean_ending(downloads: dict[date, int], day: date, window: int = WINDOW_DAYS) -> float | None:
    """Mean of the ``window`` days ending on ``day``, including ``day``."""
    values = []
    for offset in range(window - 1, -1, -1):
        key = day - timedelta(days=offset)
        if key not in downloads:
            return None
        values.append(downloads[key])
    return sum(values) / window


def fetch_pypi() -> PyPIReading:
    """Recent last_day, plus the overall moving average when that call works.

    A failure of ``/overall`` keeps ``last_day`` and leaves the average empty.
    A failure of ``/recent`` fails the source.
    """
    try:
        last_day = parse_recent(get_json(PYPI_RECENT_URL))
    except SourceError:
        raise
    except Exception as exc:
        raise SourceError("request_failed") from exc

    ma = None
    latest = None
    try:
        series = parse_overall(get_json(PYPI_OVERALL_URL))
        ma = trailing_mean(series)
        latest = series[-1][1]
    except Exception as exc:
        # The daily count is still useful. The caller notes the missing average.
        log.warning("pypi overall unavailable: %s", type(exc).__name__)
    return PyPIReading(last_day=last_day, ma_7d=ma, latest_overall=latest)
