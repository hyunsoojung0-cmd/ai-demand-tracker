"""OpenRouter daily token totals for the top 50 models plus the ``other`` row.

The dataset is CC BY 4.0. Callers must keep the attribution
``Source: OpenRouter (CC BY 4.0)``. ``total_tokens`` on each row is a string.
The latest UTC date strictly before today is the last completed day. Day-over-day
and the 7-day average both come from this response, not from the CSV.
"""

import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from ai_demand.config import OPENROUTER_LOOKBACK_DAYS, OPENROUTER_RANKINGS_URL, WINDOW_DAYS
from ai_demand.errors import SourceError
from ai_demand.http_client import get_json

log = logging.getLogger(__name__)
UTC = ZoneInfo("UTC")
OTHER_SLUG = "other"


@dataclass(frozen=True)
class OpenRouterDay:
    day: date
    total_tokens: int
    top1_model: str
    top5_share: float
    # Every row that day, including ``other``. Used to weight token prices.
    models: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class OpenRouterReading:
    day: date
    total_tokens: int
    previous_tokens: int | None
    ma_7d: float | None
    top1_model: str
    top5_share: float
    model_tokens: tuple[tuple[str, int], ...] = ()


def _tokens(value) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    return number


def daily_totals(payload: dict, today_utc: date) -> list[OpenRouterDay]:
    """One summary per completed UTC day, oldest first.

    ``other`` is included in the total and excluded from the top-1 model and
    the top-5 share. Rows dated today or later are dropped as incomplete.
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise SourceError("not_in_response")
    grouped: dict[date, list[tuple[str, int]]] = {}
    for row in data:
        if not isinstance(row, dict):
            continue
        try:
            day = date.fromisoformat(str(row["date"]))
        except (KeyError, TypeError, ValueError):
            continue
        if day >= today_utc:
            continue
        tokens = _tokens(row.get("total_tokens"))
        slug = str(row.get("model_permaslug") or "").strip()
        if tokens is None or not slug:
            continue
        grouped.setdefault(day, []).append((slug, tokens))
    summaries: list[OpenRouterDay] = []
    for day in sorted(grouped):
        rows = grouped[day]
        total = sum(tokens for _, tokens in rows)
        models = [(slug, tokens) for slug, tokens in rows if slug != OTHER_SLUG]
        if total <= 0 or not models:
            continue
        models.sort(key=lambda item: (-item[1], item[0]))
        top5 = sum(tokens for _, tokens in models[:5])
        summaries.append(
            OpenRouterDay(
                day=day,
                total_tokens=total,
                top1_model=models[0][0],
                top5_share=top5 / total,
                models=tuple(rows),
            )
        )
    if not summaries:
        raise SourceError("no_complete_day")
    return summaries


def summarize(days: list[OpenRouterDay]) -> OpenRouterReading:
    """Last completed day, its previous calendar day, and a contiguous 7-day mean."""
    latest = days[-1]
    by_day = {item.day: item for item in days}
    previous = by_day.get(latest.day - timedelta(days=1))
    window: list[OpenRouterDay] = []
    for offset in range(WINDOW_DAYS - 1, -1, -1):
        item = by_day.get(latest.day - timedelta(days=offset))
        if item is None:
            window = []
            break
        window.append(item)
    average = None if not window else sum(item.total_tokens for item in window) / WINDOW_DAYS
    return OpenRouterReading(
        day=latest.day,
        total_tokens=latest.total_tokens,
        previous_tokens=None if previous is None else previous.total_tokens,
        ma_7d=average,
        top1_model=latest.top1_model,
        top5_share=latest.top5_share,
        model_tokens=latest.models,
    )


def parse_openrouter(payload: dict, today_utc: date) -> OpenRouterReading:
    return summarize(daily_totals(payload, today_utc))


def fetch_openrouter(today_utc: date | None = None) -> OpenRouterReading:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise SourceError("missing_openrouter_key")
    today = today_utc or datetime.now(UTC).date()
    end = today - timedelta(days=1)
    start = end - timedelta(days=OPENROUTER_LOOKBACK_DAYS - 1)
    try:
        payload = get_json(
            OPENROUTER_RANKINGS_URL,
            params={"start_date": start.isoformat(), "end_date": end.isoformat()},
            headers={"Authorization": f"Bearer {key}"},
        )
    except SourceError:
        raise
    except Exception as exc:
        log.warning("openrouter request failed: %s", type(exc).__name__)
        raise SourceError("request_failed") from None
    return parse_openrouter(payload, today)
