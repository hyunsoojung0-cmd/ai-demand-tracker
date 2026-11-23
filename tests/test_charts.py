from datetime import date, timedelta

from ai_demand.charts import _trailing_ma, aligned_values, render_charts
from ai_demand.commentary import merge_history


def _rows(start: date, count: int, **columns: list) -> list[dict[str, str]]:
    rows = []
    for offset in range(count):
        row = {"date_kst": (start + timedelta(days=offset)).isoformat()}
        for key, values in columns.items():
            value = values[offset]
            row[key] = "" if value is None else str(value)
        rows.append(row)
    return rows


def test_dashboard_charts_are_pngs_and_short_series_are_skipped(tmp_path):
    start = date(2026, 11, 1)
    count = 10
    rows = _rows(
        start,
        count,
        or_total_tokens=[1_000_000_000_000 + index * 1_000_000_000 for index in range(count)],
        vast_h100_median=[2 + index * 0.01 for index in range(count)],
        vast_h200_median=[4] * count,
        vast_b200_median=[8] * 4 + [None] + [8] * 5,
        pjm_overnight_mw=[80000 + index for index in range(count)],
        pjm_overnight_7d_ma=[80000] * count,
        spot_use1_p5=[None] * 8 + [0, 1],
        spot_use1_p4d=[None] * 8 + [2, 3],
        spot_usw2_p5=[None] * 8 + [0, 0],
        spot_usw2_p4d=[None] * 8 + [1, 1],
        vast_h100_avail=[None] * 8 + [10, 12],
        vast_h200_avail=[None] * 8 + [8, 9],
        vast_b200_avail=[None] * 8 + [4, 4],
    )
    end = start + timedelta(days=count - 1)
    made = render_charts(merge_history(rows, []), tmp_path, end, days=30, korean=False)
    assert [item.path.name for item in made] == [
        "openrouter.png",
        "vast_price.png",
        "vast_avail.png",
        "pjm.png",
    ]
    assert "spot.png" not in {item.path.name for item in made}
    for item in made:
        data = item.path.read_bytes()
        assert data.startswith(b"\x89PNG\r\n\x1a\n")
        assert len(data) > 3000


def test_availability_chart_waits_for_two_days_and_spot_is_not_drawn(tmp_path):
    day = date(2026, 11, 16)
    rows = [
        {
            "date_kst": day.isoformat(),
            "or_total_tokens": "1000",
            "vast_h100_median": "2",
            "pjm_overnight_mw": "80000",
            "spot_use1_p5": "1",
            "vast_h100_avail": "3",
        }
    ]
    names = {item.path.name for item in render_charts(merge_history(rows, []), tmp_path, day, days=10, korean=False)}
    assert names == {"openrouter.png", "vast_price.png", "pjm.png"}
    assert "spot.png" not in names


def test_weighted_price_chart_waits_for_two_days(tmp_path):
    day = date(2026, 11, 16)
    one = [{"date_kst": day.isoformat(), "or_weighted_price": "1.5", "or_total_tokens": "1000", "vast_h100_median": "2", "pjm_overnight_mw": "80000"}]
    names = {item.path.name for item in render_charts(merge_history(one, []), tmp_path / "one", day, days=10, korean=False)}
    assert "or_price.png" not in names
    two = [
        {"date_kst": (day - timedelta(days=1)).isoformat(), "or_weighted_price": "1.2"},
        {"date_kst": day.isoformat(), "or_weighted_price": "1.5"},
    ]
    made = render_charts(merge_history(two, []), tmp_path / "two", day, days=10, korean=False)
    assert "or_price.png" in {item.path.name for item in made}
    spend_rows = [
        {"date_kst": (day - timedelta(days=1)).isoformat(), "or_est_spend_usd": "100"},
        {"date_kst": day.isoformat(), "or_est_spend_usd": "120"},
    ]
    spend = render_charts(merge_history(spend_rows, []), tmp_path / "spend", day, days=10, korean=False)
    assert "or_spend.png" in {item.path.name for item in spend}
    one_spend = render_charts(
        merge_history([{"date_kst": day.isoformat(), "or_est_spend_usd": "120"}], []),
        tmp_path / "spend-one",
        day,
        days=10,
        korean=False,
    )
    assert "or_spend.png" not in {item.path.name for item in one_spend}


def test_empty_history_writes_no_charts(tmp_path):
    assert render_charts({}, tmp_path, date(2026, 11, 16), days=10, korean=False) == []


def test_gaps_stay_empty_and_a_filled_daily_cell_wins():
    day = date(2026, 11, 16)
    merged = merge_history(
        [{"date_kst": day.isoformat(), "vast_h100_median": "9", "or_total_tokens": ""}],
        [{"date_kst": day.isoformat(), "vast_h100_median": "1", "or_total_tokens": "5", "notes": "backfill6m"}],
    )
    _dates, prices = aligned_values(merged, day, "vast_h100_median", days=3)
    _dates, tokens = aligned_values(merged, day, "or_total_tokens", days=3)
    assert prices == [None, None, 9.0]
    assert tokens[-1] == 5.0
    gapped = []
    for offset, value in enumerate((2.0, None, 4.0)):
        gapped.append(
            {
                "date_kst": (day - timedelta(days=2 - offset)).isoformat(),
                "vast_h100_median": "" if value is None else str(value),
            }
        )
    _dates, prices = aligned_values(merge_history(gapped, []), day, "vast_h100_median", days=3)
    assert prices == [2.0, None, 4.0]
    assert _trailing_ma([1.0, 1.0, None, 1.0, 1.0, 1.0, 1.0], 3)[3] is None
    assert _trailing_ma([1.0, 1.0, 1.0], 3)[-1] == 1
