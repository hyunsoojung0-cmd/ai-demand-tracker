"""Usage-weighted OpenRouter token prices from the public model catalog.

``GET /api/v1/models`` needs no key. ``pricing.prompt`` and
``pricing.completion`` are USD per token. Rankings-daily only publishes
``total_tokens`` (prompt + completion combined), so each model is blended
``(3 * prompt + 1 * completion) / 4`` and then scaled to USD per 1M tokens.
A ``:free`` variant is zero. The weighted average uses matched tokens only.
Coverage is matched tokens divided by every token that day, including
``other``. Estimated spend prices the whole day at that matched average.
"""

import logging
import re
from dataclasses import dataclass

from ai_demand.config import (
    OPENROUTER_MODELS_URL,
    OR_FLAGSHIP_CLAUDE,
    OR_FLAGSHIP_GEMINI,
    OR_FLAGSHIP_GPT,
    OR_PRICE_COMPLETION_WEIGHT,
    OR_PRICE_PROMPT_WEIGHT,
    OR_PRICE_VARIANTS,
)
from ai_demand.errors import SourceError
from ai_demand.http_client import get_json

log = logging.getLogger(__name__)
_DATE_SUFFIX = re.compile(r"-\d{8}$")
OTHER_SLUG = "other"


@dataclass(frozen=True)
class ModelPrice:
    prompt: float
    completion: float


@dataclass(frozen=True)
class UsagePrice:
    usd_per_million: float
    coverage: float
    spend_usd: float


def blended_usd_per_million(prompt: float, completion: float) -> float:
    """3:1 prompt:completion blend, expressed in USD per 1M tokens."""
    weight = OR_PRICE_PROMPT_WEIGHT + OR_PRICE_COMPLETION_WEIGHT
    per_token = (OR_PRICE_PROMPT_WEIGHT * prompt + OR_PRICE_COMPLETION_WEIGHT * completion) / weight
    return per_token * 1_000_000


def _number(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0 or number != number:
        return None
    return number


def parse_models(payload: dict) -> dict[str, ModelPrice]:
    """Map both ``id`` and ``canonical_slug`` to a prompt/completion pair."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise SourceError("not_in_response")
    catalog: dict[str, ModelPrice] = {}
    for model in data:
        if not isinstance(model, dict):
            continue
        pricing = model.get("pricing") if isinstance(model.get("pricing"), dict) else {}
        prompt = _number(pricing.get("prompt"))
        completion = _number(pricing.get("completion"))
        if prompt is None or completion is None:
            continue
        quote = ModelPrice(prompt, completion)
        for key in (model.get("id"), model.get("canonical_slug")):
            text = str(key or "").strip()
            if text:
                catalog[text] = quote
    if not catalog:
        raise SourceError("not_in_response")
    return catalog


def fetch_price_catalog() -> dict[str, ModelPrice]:
    """Public catalog. No Authorization header."""
    try:
        payload = get_json(OPENROUTER_MODELS_URL)
    except SourceError:
        raise
    except Exception as exc:
        log.warning("openrouter models request failed: %s", type(exc).__name__)
        raise SourceError("request_failed") from None
    return parse_models(payload)


def _lookup(slug: str, catalog: dict[str, ModelPrice]) -> ModelPrice | None:
    if slug in catalog:
        return catalog[slug]
    base = slug
    for suffix in OR_PRICE_VARIANTS:
        if slug.endswith(suffix):
            base = slug[: -len(suffix)]
            break
    if base in catalog:
        return catalog[base]
    dated = _DATE_SUFFIX.sub("", base)
    if dated != base and dated in catalog:
        return catalog[dated]
    return None


def match_usd_per_million(slug: str, catalog: dict[str, ModelPrice]) -> float | None:
    """Blended USD per 1M tokens, or None when the slug cannot be priced.

    ``other`` is unmatched. A slug ending in ``:free`` is zero even when the
    paid id is in the catalog.
    """
    text = slug.strip()
    if not text or text == OTHER_SLUG:
        return None
    if text.endswith(":free"):
        return 0.0
    quote = _lookup(text, catalog)
    if quote is None:
        return None
    return blended_usd_per_million(quote.prompt, quote.completion)


def estimated_spend_usd(total_tokens: int, usd_per_million: float) -> float:
    """USD for one completed UTC day.

    ``total_tokens`` is that day's OpenRouter total (prompt + completion,
    including ``other``). ``usd_per_million`` is that same day's
    usage-weighted blended price in USD per 1,000,000 tokens. A weekly total
    is never substituted.

    spend = total_tokens × usd_per_million / 1_000_000
    """
    return total_tokens * usd_per_million / 1_000_000


def price_usage(
    rows: tuple[tuple[str, int], ...] | list[tuple[str, int]],
    catalog: dict[str, ModelPrice],
    *,
    day_tokens: int | None = None,
) -> UsagePrice | None:
    """Weight one completed day's rows. ``other`` lowers coverage and is not priced.

    ``day_tokens`` is that day's total when the caller already has it. It must
    be the same day as ``rows``. When omitted, the sum of ``rows`` is the total.
    """
    total = 0
    matched = 0
    weighted = 0.0
    for slug, tokens in rows:
        if tokens < 0:
            continue
        total += tokens
        price = match_usd_per_million(slug, catalog)
        if price is None:
            continue
        matched += tokens
        weighted += tokens * price
    if total <= 0 or matched <= 0:
        return None
    per_million = weighted / matched
    spend_tokens = total if day_tokens is None else day_tokens
    return UsagePrice(
        usd_per_million=per_million,
        coverage=matched / total,
        spend_usd=estimated_spend_usd(spend_tokens, per_million),
    )


def flagship_usd_per_million(catalog: dict[str, ModelPrice], model_ids: tuple[str, ...]) -> float | None:
    """First configured id that exists, blended the same way as the weighted price."""
    for model_id in model_ids:
        quote = catalog.get(model_id)
        if quote is not None:
            return blended_usd_per_million(quote.prompt, quote.completion)
    return None


def flagship_prices(catalog: dict[str, ModelPrice]) -> tuple[float | None, float | None, float | None]:
    return (
        flagship_usd_per_million(catalog, OR_FLAGSHIP_GPT),
        flagship_usd_per_million(catalog, OR_FLAGSHIP_CLAUDE),
        flagship_usd_per_million(catalog, OR_FLAGSHIP_GEMINI),
    )
