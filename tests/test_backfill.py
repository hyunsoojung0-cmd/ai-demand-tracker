from datetime import date, timedelta

from ai_demand.backfill import backfill, build_backfill_row
from ai_demand.config import HISTORY_COLUMNS
from ai_demand.history import read_rows


def test_build_backfill_row_blanks_everything_except_medians_and_vllm():
    downloads = {date(2026, 11, 2) - timedelta(days=offset): 70 for offset in range(7)}
    row = build_backfill_row(
        date(2026, 11, 2),
        {"h100": 1.8, "h200": None, "b200": 8},
        downloads,
    )
    assert row["notes"] == "backfill; vast h200 median missing"
    assert row["vast_h100_median"] == "1.8"
    assert row["vast_h100_min"] == ""
    assert row["vast_h200_median"] == ""
    assert row["vast_b200_median"] == "8"
    assert row["vllm_last_day"] == "70"
    assert row["vllm_7d_ma"] == "70"
    assert row["overall"] == ""
    assert row["spot_use1_p5"] == ""
    assert row["pjm_overnight_mw"] == ""
    for column in HISTORY_COLUMNS:
        assert row[column] != "None"


def test_backfill_command_is_idempotent(tmp_path, monkeypatch):
    days = []
    day = date(2026, 10, 27)
    while day <= date(2026, 11, 14):
        days.append(day)
        day += timedelta(days=1)
    gpus = {}
    for slug, median in (("H100-SXM", 1.8), ("H200", 3.5), ("B200", 8)):
        gpus[slug] = {
            "slug": slug,
            "current": {"min": 1, "median": 2},
            "daily": [{"date": item.isoformat(), "median": median} for item in days],
        }
    pypi_rows = []
    for item in days:
        pypi_rows.append({"category": "with_mirrors", "date": item.isoformat(), "downloads": 9})
        pypi_rows.append({"category": "without_mirrors", "date": item.isoformat(), "downloads": 70})

    def fake_get(url, params=None):
        if "pypistats" in url:
            return {"data": pypi_rows}
        return {"gpus": gpus}

    monkeypatch.setattr("ai_demand.vast.get_json", fake_get)
    monkeypatch.setattr("ai_demand.backfill.get_json", fake_get)
    path = tmp_path / "history.csv"
    assert backfill(path) == 0
    assert backfill(path) == 0
    rows = read_rows(path)
    assert [row["date_kst"] for row in rows] == [
        (date(2026, 11, 2) + timedelta(days=offset)).isoformat() for offset in range(13)
    ]
    assert all(row["notes"] == "backfill" for row in rows)
    assert rows[0]["vast_h100_median"] == "1.8"
    assert rows[0]["vast_h200_median"] == "3.5"
    assert rows[0]["vast_b200_median"] == "8"
    assert rows[0]["vast_h100_min"] == ""
    assert rows[0]["vllm_last_day"] == "70"
    assert rows[0]["vllm_7d_ma"] == "70"
    assert rows[-1]["date_kst"] == "2026-11-14"
    assert rows[-1]["overall"] == ""
    assert rows[-1]["spot_usw2_p4d"] == ""
    assert rows[-1]["pjm_overnight_7d_ma"] == ""
