from ai_demand.config import HISTORY_COLUMNS, HISTORY_HEADER
from ai_demand.history import (
    assert_header_contract,
    format_number,
    read_rows,
    row_on_or_before,
    spot_bucket,
    upsert_rows,
    write_rows,
)
from ai_demand.model import DayView, VastQuote
from ai_demand.history import row_from_view


def _blank_row(day: str, **overrides) -> dict[str, str]:
    row = {column: "" for column in HISTORY_COLUMNS}
    row["date_kst"] = day
    row.update(overrides)
    return row


def test_header_matches_the_sheet_contract():
    assert_header_contract()
    assert HISTORY_HEADER.split(",") == list(HISTORY_COLUMNS)
    assert HISTORY_HEADER == (
        "date_kst,spot_use1_p5,spot_use1_p4d,spot_usw2_p5,spot_usw2_p4d,"
        "vast_h100_min,vast_h100_median,vast_h200_min,vast_h200_median,"
        "vast_b200_min,vast_b200_median,vllm_last_day,vllm_7d_ma,"
        "pjm_overnight_mw,pjm_overnight_7d_ma,overall,notes"
    )


def test_format_number_trims_trailing_zeros_and_blanks_missing_values():
    assert format_number(None, 4) == ""
    assert format_number(1.8, 4) == "1.8"
    assert format_number(2.667, 4) == "2.667"
    assert format_number(10.3132, 4) == "10.3132"
    assert format_number(82601.285714, 2) == "82601.29"
    assert format_number(100.0, 2) == "100"


def test_upsert_replaces_the_same_date_and_keeps_chronological_order(tmp_path):
    path = tmp_path / "history.csv"
    upsert_rows(
        path,
        [
            _blank_row("2026-11-02", vast_h100_median="1.8", notes="backfill"),
            _blank_row("2026-11-04", vast_h100_median="1.5", notes="backfill"),
        ],
    )
    upsert_rows(
        path,
        [
            _blank_row("2026-11-02", vast_h100_median="9.9", notes="replaced"),
            _blank_row("2026-11-03", vast_h100_median="2", notes="backfill"),
        ],
    )
    rows = read_rows(path)
    assert [row["date_kst"] for row in rows] == ["2026-11-02", "2026-11-03", "2026-11-04"]
    assert rows[0]["vast_h100_median"] == "9.9"
    assert rows[0]["notes"] == "replaced"
    assert rows[1]["spot_use1_p5"] == ""
    assert rows[2]["vast_h100_median"] == "1.5"
    text = path.read_text(encoding="utf-8")
    assert text.splitlines()[0] == HISTORY_HEADER
    assert "None" not in text
    assert text.endswith("\n")
    assert "\r" not in text


def test_rewriting_the_same_rows_is_byte_stable(tmp_path):
    path = tmp_path / "history.csv"
    rows = [_blank_row("2026-11-14", vast_h100_median="2", notes="backfill")]
    write_rows(path, rows)
    first = path.read_bytes()
    write_rows(path, read_rows(path))
    assert path.read_bytes() == first


def test_week_row_is_the_nearest_on_or_before_and_a_blank_does_not_walk_back():
    rows = [
        _blank_row("2026-11-07", spot_use1_p4d="1"),
        _blank_row("2026-11-08", spot_use1_p4d=""),
        _blank_row("2026-11-14", spot_use1_p4d="2"),
        _blank_row("2026-11-15", spot_use1_p4d="3"),
    ]
    assert row_on_or_before(rows, "2026-11-08")["date_kst"] == "2026-11-08"
    assert spot_bucket(row_on_or_before(rows, "2026-11-08"), "spot_use1_p4d") is None
    without_eighth = [row for row in rows if row["date_kst"] != "2026-11-08"]
    earlier = row_on_or_before(without_eighth, "2026-11-08")
    assert earlier["date_kst"] == "2026-11-07"
    assert spot_bucket(earlier, "spot_use1_p4d") == 1
    assert row_on_or_before(rows, "2026-11-01") is None
    assert spot_bucket(None, "spot_use1_p4d") is None


def test_bad_header_is_refused(tmp_path):
    path = tmp_path / "history.csv"
    path.write_text("a,b\n1,2\n", encoding="utf-8")
    try:
        read_rows(path)
    except ValueError as exc:
        assert "unexpected CSV header" in str(exc)
    else:
        raise AssertionError("bad header should fail")


def test_row_from_view_stores_raw_spot_index_and_blanks(tmp_path):
    view = DayView(
        date_kst="2026-11-15",
        spot_values={
            "spot_use1_p5": 0,
            "spot_use1_p4d": 3,
            "spot_usw2_p5": None,
            "spot_usw2_p4d": 4,
        },
        spot_labels={},
        spot_reason="",
        vast={"h100": VastQuote(1.6669, 2.667), "h200": VastQuote(None, None), "b200": VastQuote(None, 8)},
        vast_prev={},
        vast_reason="",
        vllm_last_day=73713,
        vllm_ma=82601.285714,
        vllm_reason="",
        pjm_mw=None,
        pjm_prev=None,
        pjm_ma=None,
        pjm_reason="",
        overall="Stable",
    )
    path = tmp_path / "history.csv"
    upsert_rows(path, [row_from_view(view, "pjm: EIA_API_KEY missing")])
    row = read_rows(path)[0]
    assert row["spot_use1_p5"] == "0"
    assert row["spot_use1_p4d"] == "3"
    assert row["spot_usw2_p5"] == ""
    assert row["spot_usw2_p4d"] == "4"
    assert row["vast_h100_min"] == "1.6669"
    assert row["vast_h100_median"] == "2.667"
    assert row["vast_h200_min"] == ""
    assert row["vast_h200_median"] == ""
    assert row["vast_b200_median"] == "8"
    assert row["vllm_last_day"] == "73713"
    assert row["vllm_7d_ma"] == "82601.29"
    assert row["pjm_overnight_mw"] == ""
    assert row["overall"] == "Stable"
    assert row["notes"] == "pjm: EIA_API_KEY missing"
