from dataclasses import replace

from ai_demand.config import SHEET_URL
from ai_demand.message import render, spot_history_suffix
from ai_demand.model import DayView, VastQuote
from ai_demand.score import score_view

EXPECTED = """\
📊 [AI Demand Daily] 2026-11-15
1. OpenRouter 일일 토큰: 데이터 없음 (요청 실패)
2. Vast.ai 중간 가격: H100 $2.67 (Δ +33.3%) · H200 $5.19 (Δ +48.3%) · B200 $10.31 (Δ +28.9%)
3. Vast.ai 대여 가능: 데이터 없음 (요청 실패)
4. PJM 야간 기저부하: 데이터 없음 (EIA_API_KEY 없음)
5. OpenRouter 가중 가격: 데이터 없음 (요청 실패)
6. OpenRouter 추정 지출: 데이터 없음 (요청 실패)
종합: Stable
📝 판단 근거: 1개 지표 중 강세 1 · 중립 0 · 약세 0
   Vast 중간가 상승
🔄 주요 변동: Vast H200 +48%, Vast H100 +33%, Vast B200 +29%
💡 해설: 뚜렷한 수급 신호가 없어 추이를 더 지켜봅니다.
📈 시트: SHEET
Data: Vast.ai
Source: OpenRouter (CC BY 4.0)""".replace("SHEET", SHEET_URL)


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
    assert "&lt;" not in text
    assert "**" not in text


def test_spot_history_suffix_formats_each_case():
    assert spot_history_suffix(3, 2, 1) == " ↑ (전일 3단계, 7일전 2단계)"
    assert spot_history_suffix(3, 3, 1) == " → (전일 동일, 7일전 2단계)"
    assert spot_history_suffix(0, 1, None) == " ↓ (전일 2단계)"
    assert spot_history_suffix(4, None, 2) == " (7일전 3단계)"
    assert spot_history_suffix(0, None, None) == ""


def test_spot_lines_show_prior_day_and_week_ago_stages():
    view = _snapshot(
        spot_prev={
            "spot_use1_p5": 0,
            "spot_use1_p4d": 2,
            "spot_usw2_p5": 1,
            "spot_usw2_p4d": 4,
        },
        spot_week={
            "spot_use1_p5": 0,
            "spot_use1_p4d": 1,
            "spot_usw2_p4d": 2,
        },
        overall="Stable",
    )
    text = render(view)
    assert "AWS Spot" not in text
    assert "us-east-1 p5" not in text


def test_spot_line_without_history_has_no_parenthetical():
    text = render(_snapshot(overall="Stable"))
    assert "AWS Spot" not in text
    assert "7일전" not in text
    assert "↑" not in text


def test_missing_spot_value_does_not_gain_a_history_suffix():
    view = _snapshot(
        spot_values={
            "spot_use1_p5": 0,
            "spot_use1_p4d": 3,
            "spot_usw2_p5": None,
            "spot_usw2_p4d": 4,
        },
        spot_prev={"spot_usw2_p5": 2},
        spot_week={"spot_usw2_p5": 1},
        overall="Stable",
    )
    assert "AWS Spot" not in render(view)


def test_openrouter_is_one_line_and_sheet_series_stay_out_of_the_message():
    from datetime import date

    view = _snapshot(
        or_total=1_234_500_000_000,
        or_previous=1_209_109_696_376,
        or_ma=1_187_019_230_769,
        or_top5_share=0.624,
        or_top1="anthropic/claude-sonnet",
        or_date=date(2026, 11, 14),
        hf_30d=68_500_000,
        hf_alltime=2_000_000_000,
        hf_implied=2_345_678,
        hf_implied_ma=2_345_678 / 1.032,
        hf_basket_size=13,
        vllm_last_day=73713,
        overall="Stable",
    )
    text = render(view)
    assert "1. OpenRouter 일일 토큰: 1,234.5B (11/14 UTC, 전일 대비 +2.1%, 7일 평균 대비 +4.0%)" in text
    assert "vLLM" not in text
    assert "Hugging Face" not in text
    assert "1위 모델" not in text
    assert text.endswith("Source: OpenRouter (CC BY 4.0)")


def test_missing_previous_median_and_missing_minimum_keep_the_line_readable():
    view = _snapshot(
        vast={"h100": VastQuote(None, 2.0), "h200": VastQuote(None, None), "b200": VastQuote(1.5, None)},
        vast_prev={"h100": None, "h200": None, "b200": None},
        vast_reason="응답에 없음",
        overall="Stable",
    )
    text = render(view)
    assert "H100 $2.00" in text
    assert "H200: 데이터 없음 (응답에 없음)" in text
    assert "B200: 데이터 없음 (응답에 없음)" in text


def test_pjm_line_shows_both_comparisons():
    view = _snapshot(
        pjm_mw=10000,
        pjm_prev=9000,
        pjm_ma=8000,
        overall="Stable",
    )
    text = render(view)
    assert "4. PJM 야간 기저부하: 10,000 MW (전일 대비 +11.1%, 7일 평균 대비 +25.0%)" in text


def test_pjm_without_a_baseline_still_shows_the_megawatts():
    view = _snapshot(pjm_mw=85000, pjm_prev=None, pjm_ma=None, overall="Stable")
    text = render(view)
    assert "4. PJM 야간 기저부하: 85,000 MW (전일 대비 데이터 없음, 7일 평균 대비 데이터 없음)" in text
