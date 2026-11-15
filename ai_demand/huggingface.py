"""Hugging Face download basket for a fixed list of official flagship models.

``downloads`` is the rolling 30-day count and ``downloadsAllTime`` is the
all-time count. The all-time sum is stored only when every model responds, so
the implied daily series never mixes a partial basket with a full one. A
partial 30-day sum is still reported, with the failure count attached.
``HF_TOKEN`` is optional and is sent only when it is set.
"""

import logging
import os
from dataclasses import dataclass
from datetime import date, timedelta

from ai_demand.config import HF_BASKET, HUGGINGFACE_MODEL_URL, WINDOW_DAYS
from ai_demand.errors import SourceError
from ai_demand.http_client import get_json

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class HuggingFaceReading:
    downloads_30d: int
    alltime: int | None
    failed: int
    basket_size: int


def parse_model_downloads(payload: dict) -> tuple[int, int]:
    if not isinstance(payload, dict):
        raise SourceError("not_in_response")
    try:
        downloads_30d = int(payload["downloads"])
        alltime = int(payload["downloadsAllTime"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SourceError("not_in_response") from exc
    if downloads_30d < 0 or alltime < 0:
        raise SourceError("not_in_response")
    return downloads_30d, alltime


def implied_download(alltime: int | None, previous_alltime: int | None) -> int | None:
    """Today's all-time basket minus the previous CSV row. Blank until both exist."""
    if alltime is None or previous_alltime is None:
        return None
    return alltime - previous_alltime


def implied_moving_average(
    history: dict[date, int],
    today: date,
    implied: int | None,
    window: int = WINDOW_DAYS,
) -> float | None:
    """Mean of ``window`` contiguous implied-daily values ending today."""
    if implied is None:
        return None
    values: list[int] = []
    for offset in range(window - 1, 0, -1):
        day = today - timedelta(days=offset)
        if day not in history:
            return None
        values.append(history[day])
    values.append(implied)
    return sum(values) / window


def fetch_huggingface() -> HuggingFaceReading:
    token = os.environ.get("HF_TOKEN", "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else None
    downloads_30d: list[int] = []
    alltime: list[int] = []
    failed = 0
    for repo_id in HF_BASKET:
        url = (
            HUGGINGFACE_MODEL_URL.format(repo_id=repo_id)
            + "?expand[]=downloads&expand[]=downloadsAllTime"
        )
        try:
            payload = get_json(url, headers=headers)
            recent, lifetime = parse_model_downloads(payload)
        except Exception as exc:
            failed += 1
            log.warning("huggingface %s failed: %s", repo_id, type(exc).__name__)
            continue
        downloads_30d.append(recent)
        alltime.append(lifetime)
    if not downloads_30d:
        raise SourceError("request_failed")
    return HuggingFaceReading(
        downloads_30d=sum(downloads_30d),
        alltime=None if failed else sum(alltime),
        failed=failed,
        basket_size=len(HF_BASKET),
    )
