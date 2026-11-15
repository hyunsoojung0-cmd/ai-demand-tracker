from datetime import date, timedelta

import pytest

from ai_demand.errors import SourceError
from ai_demand.huggingface import (
    fetch_huggingface,
    implied_download,
    implied_moving_average,
    parse_model_downloads,
)


def test_parse_model_downloads_accepts_the_expanded_fields():
    assert parse_model_downloads({"downloads": 12, "downloadsAllTime": "40"}) == (12, 40)


def test_parse_model_downloads_rejects_a_missing_count():
    with pytest.raises(SourceError):
        parse_model_downloads({"downloads": 12})


def test_implied_download_and_its_moving_average():
    assert implied_download(1500, 1000) == 500
    assert implied_download(1500, None) is None
    assert implied_download(None, 1000) is None
    today = date(2026, 11, 15)
    history = {today - timedelta(days=offset): 100 for offset in range(1, 7)}
    assert implied_moving_average(history, today, 170) == pytest.approx((100 * 6 + 170) / 7)
    assert implied_moving_average(history, today, None) is None
    del history[today - timedelta(days=3)]
    assert implied_moving_average(history, today, 170) is None


def test_a_failed_model_blanks_alltime_but_keeps_the_partial_30d_sum(monkeypatch):
    calls = {"n": 0}

    def fake_get(url, params=None, headers=None):
        calls["n"] += 1
        if "Llama-3.1-8B" in url:
            raise RuntimeError("gated")
        return {"downloads": 10, "downloadsAllTime": 100}

    monkeypatch.setattr("ai_demand.huggingface.get_json", fake_get)
    reading = fetch_huggingface()
    assert calls["n"] == 13
    assert reading.failed == 1
    assert reading.basket_size == 13
    assert reading.downloads_30d == 120
    assert reading.alltime is None


def test_every_model_failing_fails_the_source(monkeypatch):
    def fake_get(url, params=None, headers=None):
        raise RuntimeError("down")

    monkeypatch.setattr("ai_demand.huggingface.get_json", fake_get)
    with pytest.raises(SourceError) as caught:
        fetch_huggingface()
    assert caught.value.code == "request_failed"
