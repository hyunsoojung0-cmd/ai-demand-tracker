from datetime import date

from ai_demand.errors import SourceError
from ai_demand.model import DayView
from ai_demand.openrouter import parse_openrouter
from ai_demand.openrouter_price import (
    ModelPrice,
    blended_usd_per_million,
    flagship_usd_per_million,
    match_usd_per_million,
    parse_models,
    price_usage,
)
from ai_demand.score import score_view
import pytest


def _catalog() -> dict[str, ModelPrice]:
    return {
        "openai/gpt-test": ModelPrice(0.000004, 0.000012),
        "openai/gpt-test-20261101": ModelPrice(0.000004, 0.000012),
        "anthropic/claude-test": ModelPrice(0.000008, 0.000024),
        "vendor/paid": ModelPrice(0.000002, 0.000006),
    }


def test_blend_is_three_parts_prompt_and_one_part_completion():
    # (3 * 4 + 1 * 12) / 4 = 6 USD per 1M tokens.
    assert blended_usd_per_million(0.000004, 0.000012) == pytest.approx(6)


def test_free_variant_is_zero_and_suffixes_use_the_base_price():
    catalog = _catalog()
    assert match_usd_per_million("vendor/paid:free", catalog) == 0
    assert match_usd_per_million("vendor/paid:extended", catalog) == pytest.approx(3)
    assert match_usd_per_million("openai/gpt-test-20261101", catalog) == pytest.approx(6)
    assert match_usd_per_million("openai/gpt-test-20261120", catalog) == pytest.approx(6)
    assert match_usd_per_million("other", catalog) is None
    assert match_usd_per_million("missing/model", catalog) is None


def test_weighted_price_covers_matched_tokens_and_prices_the_whole_day():
    catalog = _catalog()
    # 50 at $6, 50 free at $0, 25 other unmatched. Total 125. Matched 100.
    rows = (("openai/gpt-test", 50), ("vendor/paid:free", 50), ("other", 25))
    priced = price_usage(rows, catalog)
    assert priced is not None
    assert priced.usd_per_million == pytest.approx(3)  # (50*6 + 50*0) / 100
    assert priced.coverage == pytest.approx(100 / 125)
    assert priced.spend_usd == pytest.approx(125 * 3 / 1_000_000)


def test_flagship_uses_the_first_configured_id():
    catalog = _catalog()
    assert flagship_usd_per_million(catalog, ("missing/one", "openai/gpt-test")) == pytest.approx(6)
    assert flagship_usd_per_million(catalog, ("missing/one",)) is None


def test_parse_models_indexes_id_and_canonical_slug():
    catalog = parse_models(
        {
            "data": [
                {
                    "id": "openai/gpt-test",
                    "canonical_slug": "openai/gpt-test-20261101",
                    "pricing": {"prompt": "0.000004", "completion": "0.000012"},
                }
            ]
        }
    )
    assert match_usd_per_million("openai/gpt-test", catalog) == pytest.approx(6)
    assert match_usd_per_million("openai/gpt-test-20261101", catalog) == pytest.approx(6)
    with pytest.raises(SourceError):
        parse_models({"data": []})


def test_rankings_rows_are_kept_for_the_completed_day():
    today = date(2026, 11, 15)
    rows = [
        {"date": "2026-11-14", "model_permaslug": "openai/gpt-test", "total_tokens": "40"},
        {"date": "2026-11-14", "model_permaslug": "other", "total_tokens": "10"},
        {"date": "2026-11-15", "model_permaslug": "openai/gpt-test", "total_tokens": "999"},
    ]
    reading = parse_openrouter({"data": rows}, today)
    assert reading.model_tokens == (("openai/gpt-test", 40), ("other", 10))


def test_weighted_price_does_not_change_the_verdict():
    view = DayView(
        date_kst="2026-11-15",
        spot_values={"spot_use1_p5": 2, "spot_use1_p4d": 2, "spot_usw2_p5": 2, "spot_usw2_p4d": 2},
        spot_labels={},
        spot_reason="",
        vast={},
        vast_prev={},
        vast_reason="",
        vllm_last_day=None,
        vllm_ma=None,
        vllm_reason="",
        pjm_mw=None,
        pjm_prev=None,
        pjm_ma=None,
        pjm_reason="",
        or_total=100,
        or_ma=100,
        or_weighted_price=1.5,
        or_weighted_prev=3.0,
    )
    names = [part.name for part in score_view(view, [2, 2, 2, 2]).signals]
    assert names == ["spot", "openrouter"]
    assert "or_weighted_price" not in names
