from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from ai_demand.backfill6m import (
    BACKFILL_6M_CSV,
    NOTES,
    backfill_6m,
    build_backfill6m_rows,
    date_windows,
    fetch_openrouter_days,
    inclusive_dates,
    yesterday_kst,
)
from ai_demand.config import HISTORY_COLUMNS, HISTORY_HEADER
from ai_demand.errors import SourceError
from ai_demand.history import read_rows
from ai_demand.openrouter import OpenRouterDay
from ai_demand.pjm import classify_overnight, fetch_pjm_history, seven_night_mean


def test_inclusive_dates_cover_the_default_span():
    days = inclusive_dates(date(2026, 5, 15), date(2026, 11, 14))
    assert days[0] == date(2026, 5, 15)
    assert days[-1] == date(2026, 11, 14)
    assert len(days) == 184
    assert days[1] - days[0] == timedelta(days=1)


def test_inclusive_dates_reject_a_reversed_range():
    with pytest.raises(ValueError):
        inclusive_dates(date(2026, 11, 14), date(2026, 5, 15))


def test_openrouter_windows_stay_within_366_days():
    short = date_windows(date(2026, 5, 15), date(2026, 11, 14))
    assert short == [(date(2026, 5, 15), date(2026, 11, 14))]
    long = date_windows(date(2026, 1, 1), date(2027, 1, 2))
    assert long == [
        (date(2026, 1, 1), date(2027, 1, 1)),
        (date(2027, 1, 2), date(2027, 1, 2)),
    ]
    for start, end in long:
        assert (end - start).days + 1 <= 366


def test_yesterday_kst_uses_seoul_not_utc():
    # 2026-11-15 15:30 UTC is already 2026-11-16 in Seoul, so yesterday there is the 15th.
    moment = datetime(2026, 11, 15, 15, 30, tzinfo=ZoneInfo("UTC"))
    assert yesterday_kst(moment) == date(2026, 11, 15)
    evening = datetime(2026, 11, 15, 21, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    assert yesterday_kst(evening) == date(2026, 11, 14)


def test_seven_night_mean_uses_the_latest_complete_nights():
    nights = {date(2026, 5, day): float(day) for day in range(1, 8)}
    assert seven_night_mean(nights, date(2026, 5, 7)) == pytest.approx(sum(range(1, 8)) / 7)
    assert seven_night_mean(nights, date(2026, 5, 6)) is None
    gapped = {date(2026, 5, day): 10.0 for day in (1, 2, 3, 4, 5, 6, 8)}
    assert seven_night_mean(gapped, date(2026, 5, 8)) == pytest.approx(10)


def test_rows_leave_spot_and_huggingface_blank_and_keep_the_header():
    start, end = date(2026, 5, 18), date(2026, 5, 25)
    downloads = {start + timedelta(days=offset): 100 + offset for offset in range(8)}
    vast = {"h100": {date(2026, 5, 20): 1.8}, "h200": {}, "b200": {}}
    nights = {start + timedelta(days=offset): 1000.0 + offset for offset in range(8)}
    rankings = {
        date(2026, 5, 20): OpenRouterDay(
            day=date(2026, 5, 20),
            total_tokens=123,
            top1_model="alpha/model",
            top5_share=0.5,
        )
    }
    rows = build_backfill6m_rows(
        start,
        end,
        downloads=downloads,
        vast_daily=vast,
        nights=nights,
        openrouter_days=rankings,
    )
    assert [row["date_kst"] for row in rows] == [day.isoformat() for day in inclusive_dates(start, end)]
    assert all(row["notes"] == NOTES for row in rows)
    blank = [column for column in HISTORY_COLUMNS if column not in {
        "date_kst",
        "notes",
        "vllm_last_day",
        "vllm_7d_ma",
        "vast_h100_median",
        "vast_h200_median",
        "vast_b200_median",
        "pjm_overnight_mw",
        "pjm_overnight_7d_ma",
        "or_total_tokens",
        "or_top5_share",
        "or_top1_model",
    }]
    for row in rows:
        for column in blank:
            assert row[column] == ""
        assert row["overall"] == ""
        assert row["spot_use1_p5"] == ""
        assert row["hf_basket_alltime"] == ""
        assert row["vast_h100_min"] == ""
    # May 18 is the first download, so its 7-day average is still blank.
    assert rows[0]["vllm_last_day"] == "100"
    assert rows[0]["vllm_7d_ma"] == ""
    assert rows[-1]["vllm_last_day"] == "107"
    assert rows[-1]["vllm_7d_ma"] == "104"
    assert rows[2]["vast_h100_median"] == "1.8"
    assert rows[0]["vast_h100_median"] == ""
    assert rows[0]["pjm_overnight_7d_ma"] == ""
    assert rows[-1]["pjm_overnight_mw"] == "1007"
    assert rows[-1]["or_total_tokens"] == ""
    ranked = rows[2]
    assert ranked["or_total_tokens"] == "123"
    assert ranked["or_top5_share"] == "0.5"
    assert ranked["or_top1_model"] == "alpha/model"
    assert list(rows[0]) == list(HISTORY_COLUMNS)
    assert ",".join(HISTORY_COLUMNS) == HISTORY_HEADER


def test_backfill_does_not_touch_history_and_isolates_source_failures(tmp_path, monkeypatch):
    history = tmp_path / "history.csv"
    history.write_text("do-not-touch\n", encoding="utf-8")
    target = tmp_path / "backfill_6m.csv"

    def pypi(url, params=None, headers=None):
        return {
            "data": [
                {"category": "with_mirrors", "date": "2026-05-18", "downloads": 1},
                {"category": "without_mirrors", "date": "2026-05-18", "downloads": 70},
                {"category": "without_mirrors", "date": "2026-05-19", "downloads": 80},
            ]
        }

    def boom(*args, **kwargs):
        raise RuntimeError("down")

    monkeypatch.setattr("ai_demand.backfill6m.get_json", pypi)
    monkeypatch.setattr("ai_demand.backfill6m.fetch_public_payload", boom)
    monkeypatch.setattr("ai_demand.backfill6m.fetch_pjm_history", boom)
    monkeypatch.setattr("ai_demand.backfill6m.fetch_openrouter_days", boom)
    code = backfill_6m(date(2026, 5, 18), date(2026, 5, 19), target, today_utc=date(2026, 5, 20))
    assert code == 0
    assert history.read_text(encoding="utf-8") == "do-not-touch\n"
    rows = read_rows(target)
    assert [row["date_kst"] for row in rows] == ["2026-05-18", "2026-05-19"]
    assert rows[0]["vllm_last_day"] == "70"
    assert rows[0]["notes"] == "backfill6m"
    assert rows[0]["vast_h100_median"] == ""
    assert rows[0]["pjm_overnight_mw"] == ""
    assert rows[0]["or_total_tokens"] == ""
    assert rows[0]["spot_usw2_p4d"] == ""
    assert rows[0]["hf_implied_daily"] == ""
    assert target.read_text(encoding="utf-8").splitlines()[0] == HISTORY_HEADER


def test_backfill_refuses_the_daily_history_file(tmp_path, monkeypatch):
    history = tmp_path / "history.csv"
    history.write_text("safe\n", encoding="utf-8")
    monkeypatch.setattr("ai_demand.backfill6m.DEFAULT_CSV", history)
    assert backfill_6m(path=history) == 1
    assert history.read_text(encoding="utf-8") == "safe\n"


def test_pjm_history_paginates_under_the_5000_row_cap(monkeypatch):
    calls = []

    def fake(url, params=None, headers=None):
        calls.append(url)
        offset = int(url.split("offset=")[1].split("&")[0])
        if offset == 0:
            data = [
                {"period": "2026-05-15T05", "value": "10", "respondent": "PJM", "type": "D"},
                {"period": "2026-05-15T06", "value": "20", "respondent": "PJM", "type": "D"},
            ]
        else:
            data = [{"period": "2026-05-15T07", "value": "30", "respondent": "PJM", "type": "D"}]
        return {"response": {"total": "3", "data": data}}

    monkeypatch.setenv("EIA_API_KEY", "secret-key")
    monkeypatch.setattr("ai_demand.pjm.get_json", fake)
    points = fetch_pjm_history(date(2026, 5, 15), date(2026, 5, 16), page_length=2)
    assert len(points) == 3
    assert len(calls) == 2
    assert "length=2" in calls[0]
    assert "offset=0" in calls[0]
    assert "offset=2" in calls[1]
    assert "start=2026-05-09T00" in calls[0]
    assert "end=2026-05-17T12" in calls[0]
    assert "data[0]=value" in calls[0]
    assert "facets[respondent][]=PJM" in calls[0]
    assert "%5B" not in calls[0]


def test_six_month_pjm_history_is_chunked_and_parses_a_realistic_night(monkeypatch):
    """Run 43716184862 timed out on one length=5000 call. Keep each window small."""
    import re
    from urllib.parse import parse_qs, urlparse

    calls = []

    def fake(url, params=None, headers=None):
        calls.append(url)
        query = parse_qs(urlparse(url).query)
        start = query["start"][0]
        end = query["end"][0]
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}", start)
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}", end)
        assert int(query["length"][0]) <= 1000
        assert query["length"][0] != "5000"
        span = datetime.strptime(end, "%Y-%m-%dT%H") - datetime.strptime(start, "%Y-%m-%dT%H")
        assert span <= timedelta(days=14)
        # 2026-11-14 00:00-04:00 Eastern (EST, UTC-5) is 05:00-09:00 UTC.
        # A later hour and a different respondent must not enter the night.
        wanted = {
            "2026-11-14T05": "82000",
            "2026-11-14T06": "84000",
            "2026-11-14T07": "86028",
            "2026-11-14T08": "88000",
            "2026-11-14T09": "90115",
        }
        data = []
        for period, value in wanted.items():
            if start <= period <= end:
                data.append(
                    {
                        "period": period,
                        "respondent": "PJM",
                        "respondent-name": "PJM Interconnection, LLC",
                        "type": "D",
                        "type-name": "Demand",
                        "value": value,
                        "value-units": "megawatthours",
                    }
                )
        if start <= "2026-11-14T15" <= end:
            data.append(
                {
                    "period": "2026-11-14T15",
                    "respondent": "PJM",
                    "type": "D",
                    "type-name": "Demand",
                    "value": "120000",
                    "value-units": "megawatthours",
                }
            )
            data.append(
                {
                    "period": "2026-11-14T05",
                    "respondent": "MISO",
                    "type": "D",
                    "value": "1",
                    "value-units": "megawatthours",
                }
            )
        return {
            "response": {
                "total": str(len(data)),
                "dateFormat": 'YYYY-MM-DD"T"HH24',
                "frequency": "hourly",
                "data": data,
            }
        }

    monkeypatch.setenv("EIA_API_KEY", "secret-key")
    monkeypatch.setattr("ai_demand.pjm.get_json", fake)
    points = fetch_pjm_history(date(2026, 5, 15), date(2026, 11, 14))
    nights, _incomplete = classify_overnight(points)
    assert len(calls) > 1
    # (82000 + 84000 + 86028 + 88000 + 90115) / 5
    assert nights[date(2026, 11, 14)] == pytest.approx(86028.6)


def test_pjm_history_without_a_key_does_not_call_the_api(monkeypatch):
    monkeypatch.delenv("EIA_API_KEY", raising=False)

    def fail(url, params=None, headers=None):
        raise AssertionError("should not request EIA")

    monkeypatch.setattr("ai_demand.pjm.get_json", fail)
    with pytest.raises(SourceError) as caught:
        fetch_pjm_history(date(2026, 5, 15), date(2026, 5, 16))
    assert caught.value.code == "missing_key"


def test_openrouter_range_is_requested_in_366_day_windows(monkeypatch):
    calls = []

    def fake(url, params=None, headers=None):
        calls.append((params, headers))
        day = params["start_date"]
        return {
            "data": [
                {"date": day, "model_permaslug": "alpha/model", "total_tokens": "40"},
                {"date": day, "model_permaslug": "other", "total_tokens": "10"},
            ]
        }

    monkeypatch.setenv("OPENROUTER_API_KEY", "route-key")
    monkeypatch.setattr("ai_demand.backfill6m.get_json", fake)
    found = fetch_openrouter_days(date(2026, 1, 1), date(2027, 1, 2), date(2027, 1, 3))
    assert [params["start_date"] for params, _headers in calls] == ["2026-01-01", "2027-01-02"]
    assert [params["end_date"] for params, _headers in calls] == ["2027-01-01", "2027-01-02"]
    assert calls[0][1]["Authorization"] == "Bearer route-key"
    assert set(found) == {date(2026, 1, 1), date(2027, 1, 2)}
    assert found[date(2026, 1, 1)].total_tokens == 50
    assert found[date(2026, 1, 1)].top1_model == "alpha/model"
    assert BACKFILL_6M_CSV.name == "backfill_6m.csv"
