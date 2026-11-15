from datetime import date, timedelta

import pytest

from ai_demand.errors import SourceError
from ai_demand.pypi_stats import mean_ending, parse_overall, parse_recent, trailing_mean


def test_parse_recent_last_day():
    assert parse_recent({"data": {"last_day": 73713, "last_week": 1}, "package": "vllm"}) == 73713


def test_parse_recent_rejects_a_missing_field():
    with pytest.raises(SourceError):
        parse_recent({"data": {}})


def test_parse_overall_keeps_only_without_mirrors():
    payload = {
        "data": [
            {"category": "with_mirrors", "date": "2026-11-14", "downloads": 99999},
            {"category": "without_mirrors", "date": "2026-11-13", "downloads": 10},
            {"category": "without_mirrors", "date": "2026-11-14", "downloads": 73713},
        ]
    }
    assert parse_overall(payload) == [
        (date(2026, 11, 13), 10),
        (date(2026, 11, 14), 73713),
    ]


def test_trailing_mean_requires_seven_contiguous_days():
    start = date(2026, 11, 1)
    series = [(start + timedelta(days=offset), 70) for offset in range(7)]
    assert trailing_mean(series) == 70

    gapped = series[:5] + [(start + timedelta(days=6), 70), (start + timedelta(days=7), 70)]
    assert trailing_mean(gapped) is None
    assert trailing_mean(series[:6]) is None


def test_mean_ending_includes_the_day_itself():
    downloads = {date(2026, 11, 2) - timedelta(days=offset): 10 * (offset + 1) for offset in range(7)}
    # days Nov 2, Nov 1, ... Oct 27 -> values 10, 20, 30, 40, 50, 60, 70
    assert mean_ending(downloads, date(2026, 11, 2)) == pytest.approx((10 + 20 + 30 + 40 + 50 + 60 + 70) / 7)
    assert mean_ending({date(2026, 11, 2): 1}, date(2026, 11, 2)) is None
