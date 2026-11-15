from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from ai_demand.errors import SourceError
from ai_demand.pjm import OVERNIGHT_HOURS, _eia_url, parse_pjm

NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def _row(period, value, respondent="PJM", kind="D"):
    return {"period": period, "value": value, "respondent": respondent, "type": kind}


def _night_rows(day: date, values):
    start = datetime(day.year, day.month, day.day, 0, 0, tzinfo=NY)
    rows = []
    for hour, value in zip(OVERNIGHT_HOURS, values):
        stamp = (start + timedelta(hours=hour)).astimezone(UTC)
        rows.append(_row(stamp.strftime("%Y-%m-%dT%H"), str(value)))
    return rows


def test_november_overnight_is_utc_05_through_09_and_ignores_other_hours():
    # 2026-11-14 00:00 America/New_York is EST (UTC-5).
    assert datetime(2026, 11, 14, 0, tzinfo=NY).astimezone(UTC).strftime("%Y-%m-%dT%H") == "2026-11-14T05"
    rows = [
        _row("2026-11-14T04", "999"),  # 23:00 Eastern the previous evening
        _row("2026-11-14T05", "10"),
        _row("2026-11-14T06", "20"),
        _row("2026-11-14T07", "30"),
        _row("2026-11-14T08", "40"),
        _row("2026-11-14T09", "50"),
        _row("2026-11-14T15", "999"),  # 10:00 Eastern
        _row("2026-11-14T05", "10", respondent="MISO"),
        _row("2026-11-14T06", None),
    ]
    reading = parse_pjm({"response": {"data": rows}})
    assert reading.night == date(2026, 11, 14)
    assert reading.overnight_mw == pytest.approx(30)
    assert reading.previous_mw is None
    assert reading.ma_7d is None


def test_july_night_follows_eastern_daylight_time():
    # 2026-07-15 00:00 America/New_York is EDT (UTC-4), so the window starts at 04:00 UTC.
    assert datetime(2026, 7, 15, 0, tzinfo=NY).astimezone(UTC).strftime("%Y-%m-%dT%H") == "2026-07-15T04"
    reading = parse_pjm({"response": {"data": _night_rows(date(2026, 7, 15), [1, 2, 3, 4, 5])}})
    assert reading.night == date(2026, 7, 15)
    assert reading.overnight_mw == pytest.approx(3)


def test_duplicate_local_hour_is_averaged_once():
    rows = _night_rows(date(2026, 11, 14), [10, 20, 30, 40, 50])
    rows.append(_row("2026-11-14T05", "30"))  # second sample of local hour 0; mean becomes 20
    reading = parse_pjm({"response": {"data": rows}})
    assert reading.overnight_mw == pytest.approx((20 + 20 + 30 + 40 + 50) / 5)


def test_seven_complete_nights_include_the_latest_in_the_average():
    rows = []
    for offset in range(7):
        day = date(2026, 11, 8) + timedelta(days=offset)
        rows.extend(_night_rows(day, [100] * 5 if offset < 6 else [170] * 5))
    reading = parse_pjm({"response": {"data": rows}})
    assert reading.night == date(2026, 11, 14)
    assert reading.overnight_mw == pytest.approx(170)
    assert reading.previous_mw == pytest.approx(100)
    assert reading.ma_7d == pytest.approx((100 * 6 + 170) / 7)
    assert reading.prior_gap is False
    assert reading.latest_incomplete is False


def test_incomplete_latest_night_falls_back_and_is_flagged():
    rows = _night_rows(date(2026, 11, 13), [80] * 5)
    rows.extend(_night_rows(date(2026, 11, 14), [10, 20, 30]))  # hours 0, 1, 2 only
    reading = parse_pjm({"response": {"data": rows}})
    assert reading.night == date(2026, 11, 13)
    assert reading.overnight_mw == pytest.approx(80)
    assert reading.latest_incomplete is True


def test_gap_between_complete_nights_is_flagged():
    rows = _night_rows(date(2026, 11, 10), [50] * 5)
    rows.extend(_night_rows(date(2026, 11, 12), [70] * 5))
    reading = parse_pjm({"response": {"data": rows}})
    assert reading.night == date(2026, 11, 12)
    assert reading.previous_mw == pytest.approx(50)
    assert reading.prior_gap is True
    assert reading.ma_7d is None


def test_no_complete_night_is_an_incomplete_source():
    with pytest.raises(SourceError) as caught:
        parse_pjm({"response": {"data": _night_rows(date(2026, 11, 14), [1, 2, 3])}})
    assert caught.value.code == "incomplete"


def test_eia_url_keeps_bracket_syntax_and_carries_the_key():
    url = _eia_url("secret-key")
    assert url.startswith("https://api.eia.gov/v2/electricity/rto/region-data/data/?")
    assert "api_key=secret-key" in url
    assert "frequency=hourly" in url
    assert "data[0]=value" in url
    assert "facets[respondent][]=PJM" in url
    assert "facets[type][]=D" in url
    assert "sort[0][column]=period" in url
    assert "sort[0][direction]=desc" in url
    assert "length=240" in url
    assert "%5B" not in url
