"""AWS Spot Advisor interruption buckets for the four GPU instances we track."""

from dataclasses import dataclass

from ai_demand.config import DEFAULT_BUCKET_LABELS, SPOT_INSTANCES, SPOT_URL
from ai_demand.errors import SourceError
from ai_demand.http_client import get_json


@dataclass(frozen=True)
class SpotReading:
    """``values`` maps a CSV column to the raw ``r`` index (0-4)."""

    values: dict[str, int]
    labels: dict[str, str]


def _bucket_labels(payload: dict) -> dict[int, str]:
    labels = dict(DEFAULT_BUCKET_LABELS)
    for item in payload.get("ranges") or []:
        if not isinstance(item, dict):
            continue
        try:
            labels[int(item["index"])] = str(item["label"])
        except (KeyError, TypeError, ValueError):
            continue
    return labels


def parse_spot(payload: dict) -> SpotReading:
    """Read ``spot_advisor[region]['Linux'][instance]['r']``.

    A region or instance that is absent is omitted. If none of the four
    instances are present, the source is unavailable.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("spot_advisor"), dict):
        raise SourceError("not_in_response")
    labels = _bucket_labels(payload)
    advisor = payload["spot_advisor"]
    values: dict[str, int] = {}
    used: dict[str, str] = {}
    for target in SPOT_INSTANCES:
        region = advisor.get(target.region)
        if not isinstance(region, dict):
            continue
        linux = region.get("Linux")
        if not isinstance(linux, dict):
            continue
        instance = linux.get(target.instance)
        if not isinstance(instance, dict) or "r" not in instance:
            continue
        try:
            bucket = int(instance["r"])
        except (TypeError, ValueError):
            continue
        values[target.column] = bucket
        used[target.column] = labels.get(bucket, str(bucket))
    if not values:
        raise SourceError("not_in_response")
    return SpotReading(values=values, labels=used)


def fetch_spot() -> SpotReading:
    try:
        payload = get_json(SPOT_URL)
    except Exception as exc:
        raise SourceError("request_failed") from exc
    return parse_spot(payload)
