from datetime import date

import pytest

from ai_demand.errors import SourceError
from ai_demand.vast import parse_bundles, parse_daily, parse_public


def test_parse_public_looks_up_slugs_not_dict_keys():
    payload = {
        "gpus": {
            "not-a-slug": {
                "slug": "H100-SXM",
                "current": {"min": 1.6669, "median": 2.667, "p10": 1.67, "available": 50},
                "daily": [{"date": "2026-11-14", "median": 2.0}],
            },
            "other": {
                "slug": "H200",
                "current": {"min": 3.5531, "median": 5.1919},
            },
            "ignored": {"slug": "H100-NVL", "current": {"min": 9, "median": 9}},
        }
    }
    quotes = parse_public(payload)
    assert set(quotes) == {"h100", "h200"}
    assert quotes["h100"].min_price == 1.6669
    assert quotes["h100"].median == 2.667
    assert quotes["h200"].median == 5.1919


def test_parse_daily_indexes_median_by_date():
    payload = {
        "gpus": {
            "h100 sxm": {
                "slug": "H100-SXM",
                "daily": [
                    {"date": "2026-11-14", "median": 2},
                    {"date": "2026-11-13", "median": 1.75},
                ],
            },
            "b200": {"slug": "B200", "daily": [{"date": "2026-11-14", "median": 8}]},
        }
    }
    daily = parse_daily(payload)
    assert daily["h100"][date(2026, 11, 14)] == 2
    assert daily["h100"][date(2026, 11, 13)] == 1.75
    assert daily["b200"][date(2026, 11, 14)] == 8


def test_parse_bundles_divides_by_gpu_count_and_takes_median():
    payload = {
        "offers": [
            {"gpu_name": "H100 SXM", "dph_total": 10, "num_gpus": 2},
            {"gpu_name": "H100 SXM", "dph_total": 3, "num_gpus": 1},
            {"gpu_name": "H100 SXM", "dph_total": 8, "num_gpus": 2},
            {"gpu_name": "H200", "dph_total": 10, "num_gpus": 2},
            {"gpu_name": "H200", "dph_total": 7, "num_gpus": 1},
            {"gpu_name": "RTX 4090", "dph_total": 1, "num_gpus": 1},
            {"gpu_name": "B200", "dph_total": 0, "num_gpus": 1},
            {"gpu_name": "B200", "dph_total": 8, "num_gpus": 0},
        ]
    }
    quotes = parse_bundles(payload)
    assert set(quotes) == {"h100", "h200"}
    assert quotes["h100"].min_price == 3
    assert quotes["h100"].median == 4
    assert quotes["h200"].min_price == 5
    assert quotes["h200"].median == 6


def test_parse_public_rejects_a_feed_without_our_gpus():
    with pytest.raises(SourceError) as caught:
        parse_public({"gpus": {"a10": {"slug": "A10", "current": {"min": 1, "median": 1}}}})
    assert caught.value.code == "not_in_response"
