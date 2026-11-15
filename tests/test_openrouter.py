from datetime import date, timedelta

import pytest

from ai_demand.errors import SourceError
from ai_demand.openrouter import parse_openrouter


def _row(day: date, slug: str, tokens: int) -> dict:
    return {"date": day.isoformat(), "model_permaslug": slug, "total_tokens": str(tokens)}


def test_parse_openrouter_sums_other_and_ranks_the_top_five():
    today = date(2026, 11, 15)
    start = date(2026, 11, 7)
    normal = [("alpha", 40), ("beta", 30), ("gamma", 10), ("delta", 5), ("epsilon", 5), ("zeta", 4), ("other", 6)]
    last = [("alpha", 44), ("beta", 33), ("gamma", 11), ("delta", 6), ("epsilon", 5), ("zeta", 4), ("other", 7)]
    rows = []
    for offset in range(8):
        day = start + timedelta(days=offset)
        counts = last if day == date(2026, 11, 14) else normal
        rows.extend(_row(day, slug, tokens) for slug, tokens in counts)
    # Today's partial day must be ignored even if it is larger.
    rows.append(_row(today, "alpha", 10**12))
    reading = parse_openrouter({"data": rows}, today)
    assert reading.day == date(2026, 11, 14)
    assert reading.total_tokens == 110
    assert reading.previous_tokens == 100
    assert reading.top1_model == "alpha"
    # Top 5 are 40+30+10+5+5 = 90 of 100 on a normal day, so 99 of 110 on the last day.
    assert reading.top5_share == pytest.approx(99 / 110)
    assert reading.ma_7d == pytest.approx((100 * 6 + 110) / 7)


def test_parse_openrouter_drops_the_average_when_a_day_is_missing():
    today = date(2026, 11, 15)
    rows = []
    for day in (date(2026, 11, 12), date(2026, 11, 14)):
        rows.append(_row(day, "alpha", 10))
        rows.append(_row(day, "other", 1))
    reading = parse_openrouter({"data": rows}, today)
    assert reading.day == date(2026, 11, 14)
    assert reading.previous_tokens is None
    assert reading.ma_7d is None


def test_parse_openrouter_rejects_an_empty_window():
    with pytest.raises(SourceError) as caught:
        parse_openrouter({"data": []}, date(2026, 11, 15))
    assert caught.value.code == "no_complete_day"
