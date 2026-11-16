"""Vast.ai H100 SXM, H200, and B200 prices.

The public feed is the source of record. If that request fails, on-demand
bundles are the fallback and ``dph_total / num_gpus`` is the per-GPU price.
"""

import json
import logging
from dataclasses import dataclass
from datetime import date

from ai_demand.config import (
    VAST_BUNDLE_NAMES,
    VAST_BUNDLE_QUERY,
    VAST_BUNDLES_URL,
    VAST_PUBLIC_URL,
    VAST_SLUGS,
)
from ai_demand.errors import SourceError
from ai_demand.http_client import get_json
from ai_demand.model import VastQuote

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class VastFetch:
    quotes: dict[str, VastQuote]
    used_fallback: bool


def _number(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def _index_public(payload: dict) -> dict[str, dict]:
    if not isinstance(payload, dict) or not isinstance(payload.get("gpus"), dict):
        raise SourceError("not_in_response")
    found: dict[str, dict] = {}
    for gpu in payload["gpus"].values():
        if not isinstance(gpu, dict):
            continue
        key = VAST_SLUGS.get(str(gpu.get("slug") or ""))
        if key is not None:
            found[key] = gpu
    return found


def parse_public(payload: dict) -> dict[str, VastQuote]:
    """Current min and median, keyed by h100 / h200 / b200. Lookup is by slug."""
    found = _index_public(payload)
    quotes: dict[str, VastQuote] = {}
    for key, gpu in found.items():
        current = gpu.get("current") if isinstance(gpu.get("current"), dict) else {}
        quotes[key] = VastQuote(
            min_price=_number(current.get("min")),
            median=_number(current.get("median")),
        )
    if not any(q.min_price is not None or q.median is not None for q in quotes.values()):
        raise SourceError("not_in_response")
    return quotes


def parse_daily(payload: dict) -> dict[str, dict[date, float]]:
    """Historical daily medians from the public feed, for the backfill only."""
    found = _index_public(payload)
    series: dict[str, dict[date, float]] = {}
    for key, gpu in found.items():
        points: dict[date, float] = {}
        for item in gpu.get("daily") or []:
            if not isinstance(item, dict):
                continue
            median = _number(item.get("median"))
            try:
                day = date.fromisoformat(str(item.get("date")))
            except ValueError:
                continue
            if median is None:
                continue
            points[day] = median
        series[key] = points
    if not series:
        raise SourceError("not_in_response")
    return series


def parse_bundles(payload: dict) -> dict[str, VastQuote]:
    """Min and median of per-GPU on-demand prices.

    Each offer contributes ``dph_total / num_gpus``. The median of an even
    count is the average of the two middle prices.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("offers"), list):
        raise SourceError("not_in_response")
    grouped: dict[str, list[float]] = {}
    for offer in payload["offers"]:
        if not isinstance(offer, dict):
            continue
        key = VAST_BUNDLE_NAMES.get(str(offer.get("gpu_name") or "").strip())
        if key is None:
            continue
        total = _number(offer.get("dph_total"))
        try:
            count = int(offer.get("num_gpus"))
        except (TypeError, ValueError):
            continue
        if total is None or count <= 0 or total <= 0:
            continue
        grouped.setdefault(key, []).append(total / count)
    quotes: dict[str, VastQuote] = {}
    for key, prices in grouped.items():
        ordered = sorted(prices)
        mid = len(ordered) // 2
        if len(ordered) % 2:
            median = ordered[mid]
        else:
            median = (ordered[mid - 1] + ordered[mid]) / 2
        quotes[key] = VastQuote(min_price=ordered[0], median=median)
    if not quotes:
        raise SourceError("not_in_response")
    return quotes


def fetch_vast() -> VastFetch:
    try:
        payload = get_json(VAST_PUBLIC_URL)
        return VastFetch(parse_public(payload), used_fallback=False)
    except Exception as exc:
        log.warning("vast public feed failed (%s); trying bundles", type(exc).__name__)
    try:
        payload = get_json(VAST_BUNDLES_URL, params={"q": json.dumps(VAST_BUNDLE_QUERY, separators=(",", ":"))})
        return VastFetch(parse_bundles(payload), used_fallback=True)
    except SourceError:
        raise
    except Exception as exc:
        raise SourceError("request_failed") from exc


AVAIL_QUERY_LIMIT = 10000


def parse_availability(payload: dict) -> dict[str, int]:
    """Distinct rentable ``machine_id`` values for H100 SXM, H200, and B200.

    A GPU with no offers is 0. A truncated page is refused so a capped count
    is not stored as if it were complete. Offers without ``machine_id`` are
    skipped rather than counted twice under another id.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("offers"), list):
        raise SourceError("not_in_response")
    if payload.get("truncated") is True:
        raise SourceError("truncated")
    seen = {key: set() for key in ("h100", "h200", "b200")}
    for offer in payload["offers"]:
        if not isinstance(offer, dict) or offer.get("rentable") is False:
            continue
        key = VAST_BUNDLE_NAMES.get(str(offer.get("gpu_name") or "").strip())
        if key not in seen:
            continue
        machine = offer.get("machine_id")
        if machine is None or machine == "":
            continue
        seen[key].add(machine)
    return {key: len(machines) for key, machines in seen.items()}


def fetch_vast_availability() -> dict[str, int]:
    """Unauthenticated bundles search. No API key.

    Same rentable on-demand filter as the price fallback, with a high limit.
    The live check on 2026-11-16 returned HTTP 200 and ``truncated: false``.
    """
    query = dict(VAST_BUNDLE_QUERY)
    query["limit"] = AVAIL_QUERY_LIMIT
    try:
        payload = get_json(VAST_BUNDLES_URL, params={"q": json.dumps(query, separators=(",", ":"))})
    except SourceError:
        raise
    except Exception as exc:
        raise SourceError("request_failed") from exc
    return parse_availability(payload)


def fetch_public_payload() -> dict:
    """Public feed for the backfill. Bundles have no daily history, so there is no fallback."""
    try:
        payload = get_json(VAST_PUBLIC_URL)
    except Exception as exc:
        raise SourceError("request_failed") from exc
    return payload
