import pytest

from ai_demand.model import DayView, VastQuote
from ai_demand.score import band_signal, score_view, spot_signal, vast_avail_signal, verdict_name


@pytest.mark.parametrize(
    ("values", "previous", "expected"),
    [
        ([3, 3, 3, 3], None, 1),
        ([1, 1, 1, 1], None, -1),
        ([2, 2, 2, 2], None, 0),
        ([2, 2, 2, 2], [0, 0, 0, 0], 1),
        ([2, 2, 2, 2], [4, 4, 4, 4], -1),
        ([4, 4, 4, 4], [4, 4, 4, 4], 1),
        ([4, 4, 4, 4], [0, 0, 0, 0], 1),
        ([3, 3, 3, 3], [4, 4, 4, 4], 0),
        ([0, 0, 0, 0], [2, 2, 2, 2], -1),
        ([1, 1, 1, 1], [0, 0, 0, 0], 0),
        ([0, 3, 0, 4], None, 0),
        ([], None, None),
        (None, None, None),
    ],
)
def test_spot_signal(values, previous, expected):
    assert spot_signal(values, previous) == expected


@pytest.mark.parametrize(
    ("current", "baseline", "up", "down", "expected"),
    [
        (103, 100, 0.03, -0.03, 0),
        (104, 100, 0.03, -0.03, 1),
        (97, 100, 0.03, -0.03, 0),
        (96, 100, 0.03, -0.03, -1),
        (105, 100, 0.05, -0.05, 0),
        (106, 100, 0.05, -0.05, 1),
        (95, 100, 0.05, -0.05, 0),
        (94, 100, 0.05, -0.05, -1),
        (101, 100, 0.01, -0.01, 0),
        (102, 100, 0.01, -0.01, 1),
        (99, 100, 0.01, -0.01, 0),
        (98, 100, 0.01, -0.01, -1),
        (110, 100, 0.10, -0.10, 0),
        (111, 100, 0.10, -0.10, 1),
        (90, 100, 0.10, -0.10, 0),
        (89, 100, 0.10, -0.10, -1),
        (None, 100, 0.03, -0.03, None),
        (110, 0, 0.03, -0.03, None),
        (110, None, 0.03, -0.03, None),
    ],
)
def test_band_signal_is_strict_about_the_threshold(current, baseline, up, down, expected):
    assert band_signal(current, baseline, up, down) == expected


def test_verdict_boundaries():
    assert verdict_name(2) == "Strong"
    assert verdict_name(3) == "Strong"
    assert verdict_name(1) == "Stable"
    assert verdict_name(0) == "Stable"
    assert verdict_name(-1) == "Stable"
    assert verdict_name(-2) == "Softening"
    assert verdict_name(-3) == "Softening"


def _view(**overrides) -> DayView:
    base = dict(
        date_kst="2026-11-15",
        spot_values={
            "spot_use1_p5": 0,
            "spot_use1_p4d": 0,
            "spot_usw2_p5": 0,
            "spot_usw2_p4d": 0,
        },
        spot_labels={},
        spot_reason="",
        vast={
            "h100": VastQuote(1, 2),
            "h200": VastQuote(1, 2),
            "b200": VastQuote(1, 100),
        },
        vast_prev={"h100": 2, "h200": 2, "b200": 1},
        vast_reason="",
        vllm_last_day=100,
        vllm_ma=100,
        vllm_reason="",
        pjm_mw=100,
        pjm_prev=100,
        pjm_ma=100,
        pjm_reason="",
    )
    base.update(overrides)
    return DayView(**base)


def test_unavailable_signals_are_left_out_and_b200_is_not_scored():
    view = _view(
        spot_values={column: None for column in ("spot_use1_p5", "spot_use1_p4d", "spot_usw2_p5", "spot_usw2_p4d")},
        vast={"h100": VastQuote(1, 3), "h200": VastQuote(None, None), "b200": VastQuote(1, 100)},
        vast_prev={"h100": 2, "h200": None, "b200": 1},
        vllm_last_day=None,
        vllm_ma=None,
        pjm_mw=None,
        pjm_ma=None,
    )
    scored = score_view(view, None)
    assert [part.name for part in scored.signals] == ["vast_price"]
    assert scored.total == 1
    assert scored.verdict == "Stable"


def test_two_up_signals_are_strong_even_when_others_are_missing():
    view = _view(
        spot_values={
            "spot_use1_p5": 4,
            "spot_use1_p4d": 4,
            "spot_usw2_p5": 4,
            "spot_usw2_p4d": 4,
        },
        vast={"h100": VastQuote(1, 3), "h200": VastQuote(None, None), "b200": VastQuote(None, None)},
        vast_prev={"h100": 2, "h200": None, "b200": None},
        vllm_last_day=None,
        pjm_mw=None,
        pjm_ma=None,
    )
    scored = score_view(view)
    assert scored.total == 1
    assert scored.verdict == "Stable"
    assert "spot" not in {part.name for part in scored.signals}
    spend = _view(
        or_est_spend_usd=120,
        or_spend_ma=100,
        vast={"h100": VastQuote(1, 3), "h200": VastQuote(None, None), "b200": VastQuote(None, None)},
        vast_prev={"h100": 2, "h200": None, "b200": None},
        vllm_last_day=None,
        pjm_mw=None,
        pjm_ma=None,
    )
    scored = score_view(spend)
    assert scored.total == 2
    assert scored.verdict == "Strong"
    assert "or_spend" in {part.name for part in scored.signals}
    assert "spot" not in {part.name for part in scored.signals}


def test_neutral_present_signals_stay_stable():
    view = _view(
        spot_values={
            "spot_use1_p5": 2,
            "spot_use1_p4d": 2,
            "spot_usw2_p5": 2,
            "spot_usw2_p4d": 2,
        }
    )
    scored = score_view(view, [2, 2, 2, 2])
    assert [part.value for part in scored.signals] == [0, 0]
    assert "spot" not in {part.name for part in scored.signals}
    assert scored.verdict == "Stable"


def test_openrouter_joins_the_score_and_hf_does_not():
    view = _view(or_total=106, or_ma=100, hf_implied=89, hf_implied_ma=100, vllm_last_day=50, vllm_ma=100)
    scored = score_view(view, [2, 2, 2, 2])
    signals = {part.name: part.value for part in scored.signals}
    assert signals["openrouter"] == 1
    assert "hf" not in signals
    assert "vllm" not in signals


def test_live_shaped_snapshot_is_stable():
    """Spot 1.75 with no history, Vast price up, PJM missing -> +1 Stable."""
    view = _view(
        spot_values={
            "spot_use1_p5": 0,
            "spot_use1_p4d": 3,
            "spot_usw2_p5": 0,
            "spot_usw2_p4d": 4,
        },
        vast={
            "h100": VastQuote(1.6669, 2.667),
            "h200": VastQuote(3.5531, 5.1919),
            "b200": VastQuote(7.5013, 10.3132),
        },
        vast_prev={"h100": 2.0, "h200": 3.5, "b200": 8.0},
        vllm_last_day=73713,
        vllm_ma=578209 / 7,
        pjm_mw=None,
        pjm_ma=None,
    )
    scored = score_view(view, None)
    assert [(part.name, part.value) for part in scored.signals] == [
        ("vast_price", 1),
    ]
    assert "spot" not in {part.name for part in scored.signals}
    assert scored.verdict == "Stable"


def test_fewer_rentable_machines_score_positive_and_the_first_day_is_neutral():
    assert vast_avail_signal({"h100": 10, "h200": 10, "b200": 10}, {"h100": 12, "h200": 12, "b200": 12}) == 1
    assert vast_avail_signal({"h100": 20, "h200": 10, "b200": 10}, {"h100": 10, "h200": 10, "b200": 10}) == -1
    assert vast_avail_signal({"h100": 10, "h200": 10, "b200": 10}, {"h100": 10, "h200": 10, "b200": 10}) == 0
    assert vast_avail_signal({"h100": 10}, {}) is None
    view = _view(
        vast_avail={"h100": 8, "h200": 8, "b200": 8},
        vast_avail_prev={"h100": 12, "h200": 12, "b200": 12},
    )
    signals = {part.name: part.value for part in score_view(view, [2, 2, 2, 2]).signals}
    assert signals["vast_avail"] == 1
