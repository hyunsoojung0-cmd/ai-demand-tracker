from dataclasses import replace

from ai_demand.config import SHEET_URL
from ai_demand.message import render
from ai_demand.model import DayView, VastQuote
from ai_demand.score import score_view

EXPECTED = """\
📊 [AI Demand Daily] 2026-11-15
1. AWS Spot 중단률
   - us-east-1 p5 (H100): 1단계 (<5%)
   - us-east-1 p4d (A100): 4단계 (15-20%)
   - us-west-2 p5 (H100): 1단계 (<5%)
   - us-west-2 p4d (A100): 5단계 (>20%)
2. Vast.ai 가격 (중간값 / 최저)
   - H100: $2.67/hr (Δ +33.3%) / 최저 $1.67
   - H200: $5.19/hr (Δ +48.3%) / 최저 $3.55
   - B200: $10.31/hr (Δ +28.9%) / 최저 $7.50
3. vLLM PyPI
   - 전일 다운로드: 73,713 (7일 평균 대비 -10.8%)
4. PJM 야간 기저부하
   - 데이터 없음 (EIA_API_KEY 없음)
종합: Stable
📈 시트: SHEET
Data: Vast.ai""".replace("SHEET", SHEET_URL)


def _snapshot(**overrides) -> DayView:
    view = DayView(
        date_kst="2026-11-15",
        spot_values={
            "spot_use1_p5": 0,
            "spot_use1_p4d": 3,
            "spot_usw2_p5": 0,
            "spot_usw2_p4d": 4,
        },
        spot_labels={
            "spot_use1_p5": "<5%",
            "spot_use1_p4d": "15-20%",
            "spot_usw2_p5": "<5%",
            "spot_usw2_p4d": ">20%",
        },
        spot_reason="응답에 없음",
        vast={
            "h100": VastQuote(1.6669, 2.667),
            "h200": VastQuote(3.5531, 5.1919),
            "b200": VastQuote(7.5013, 10.3132),
        },
        vast_prev={"h100": 2.0, "h200": 3.5, "b200": 8.0},
        vast_reason="응답에 없음",
        vllm_last_day=73713,
        vllm_ma=578209 / 7,
        vllm_reason="요청 실패",
        pjm_mw=None,
        pjm_prev=None,
        pjm_ma=None,
        pjm_reason="EIA_API_KEY 없음",
    )
    if overrides:
        view = replace(view, **overrides)
    return view


def test_message_matches_the_daily_layout_and_does_not_escape_labels():
    view = _snapshot()
    scored = score_view(view, None)
    view = replace(view, overall=scored.verdict)
    text = render(view)
    assert text == EXPECTED
    assert "<5%" in text
    assert "&lt;" not in text
    assert "**" not in text


def test_missing_previous_median_and_missing_minimum_keep_the_line_readable():
    view = _snapshot(
        vast={"h100": VastQuote(None, 2.0), "h200": VastQuote(None, None), "b200": VastQuote(1.5, None)},
        vast_prev={"h100": None, "h200": None, "b200": None},
        vast_reason="응답에 없음",
        overall="Stable",
    )
    text = render(view)
    assert "   - H100: $2.00/hr (Δ 데이터 없음) / 최저 데이터 없음" in text
    assert "   - H200: 데이터 없음 (응답에 없음)" in text
    assert "   - B200: 데이터 없음 (응답에 없음) / 최저 $1.50" in text


def test_pjm_line_shows_both_comparisons():
    view = _snapshot(
        pjm_mw=10000,
        pjm_prev=9000,
        pjm_ma=8000,
        overall="Stable",
    )
    text = render(view)
    assert "   - 야간 평균 10,000 MW (전일 대비 +11.1%, 7일 평균 대비 +25.0%)" in text


def test_pjm_without_a_baseline_still_shows_the_megawatts():
    view = _snapshot(pjm_mw=85000, pjm_prev=None, pjm_ma=None, overall="Stable")
    text = render(view)
    assert "   - 야간 평균 85,000 MW (전일 대비 데이터 없음, 7일 평균 대비 데이터 없음)" in text
