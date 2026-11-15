from datetime import date, timedelta

from ai_demand.commentary import (
    TELEGRAM_LIMIT,
    build_commentary,
    fit_telegram,
    merge_history,
    moving_average,
    overlay_live,
)
from ai_demand.message import render
from ai_demand.model import DayView, VastQuote
from ai_demand.score import Score, Signal


def _view(**overrides) -> DayView:
    base = dict(
        date_kst="2026-11-15",
        spot_values={
            "spot_use1_p5": 0,
            "spot_use1_p4d": 3,
            "spot_usw2_p5": 1,
            "spot_usw2_p4d": 4,
        },
        spot_labels={},
        spot_reason="응답에 없음",
        spot_prev={"spot_use1_p5": 0, "spot_use1_p4d": 2, "spot_usw2_p5": 2, "spot_usw2_p4d": 4},
        spot_week={"spot_use1_p5": 1, "spot_use1_p4d": 1, "spot_usw2_p5": 1, "spot_usw2_p4d": 2},
        vast={
            "h100": VastQuote(1.5, 2.2),
            "h200": VastQuote(3.0, 4.0),
            "b200": VastQuote(7.0, 8.0),
        },
        vast_prev={"h100": 2.0, "h200": 5.0, "b200": 8.0},
        vast_reason="",
        vllm_last_day=80,
        vllm_ma=100,
        vllm_reason="",
        pjm_mw=90000,
        pjm_prev=80000,
        pjm_ma=85000,
        pjm_reason="",
        or_total=1_200,
        or_previous=1_000,
        or_ma=1_000,
        or_top5_share=0.5,
        or_top1="alpha/model",
        or_date=date(2026, 11, 14),
        hf_implied=None,
        hf_implied_ma=None,
        overall="Strong",
    )
    base.update(overrides)
    return DayView(**base)


def _score(*pairs: tuple[str, int], verdict: str = "Strong") -> Score:
    return Score(total=sum(value for _name, value in pairs), verdict=verdict, signals=tuple(Signal(name, value) for name, value in pairs))


def _series(column: str, start: date, days: int, value) -> list[dict[str, str]]:
    rows = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        row = {"date_kst": day.isoformat(), column: str(value(offset) if callable(value) else value)}
        rows.append(row)
    return rows


def test_full_commentary_names_the_push_moves_and_trends():
    start = date(2026, 5, 15)
    rows = []
    for offset in range(200):
        day = start + timedelta(days=offset)
        rows.append(
            {
                "date_kst": day.isoformat(),
                "or_total_tokens": str(1000 + offset),
                "vllm_last_day": str(200 - offset // 10),
                "pjm_overnight_mw": str(80000 + offset),
                "vast_h100_median": str(2 + offset / 1000),
                "vast_h200_median": "4",
            }
        )
    view = _view()
    score = _score(
        ("vast_h100", -1),
        ("vast_h200", -1),
        ("vllm", -1),
        ("pjm", 1),
        ("openrouter", 1),
        ("spot", 0),
        verdict="Stable",
    )
    # Price down and usage up, with winter PJM and a vLLM decline.
    text = "\n".join(build_commentary(view, score, merge_history(rows, []), notes="vast: dod vs backfill"))
    assert "6개 지표 중 강세 2 · 중립 1 · 약세 3" in text
    assert "강세: PJM 야간부하 7일 평균 상회, OpenRouter 토큰 7일 평균 상회" in text
    assert "약세:" in text
    assert "Vast H100·H200 가격 하락" in text
    assert "백필 일별값과 비교" in text
    assert "하루 변동이 큼" in text
    assert "📈 추이: OpenRouter 토큰 7일평균" in text
    assert "30일 전 대비" in text
    assert "90일 전 대비" in text
    assert "vLLM 7일평균" in text
    assert "PJM 야간 7일평균" in text
    assert "Vast H100 중간가 7일평균" in text
    assert "Spot 중단률: 7일 전보다 상승 2 · 하락 1 · 보합 1" in text
    assert "수요 우위 국면" in text
    assert "난방 수요" in text
    assert "배포 채널" in text
    assert len(text.splitlines()) <= 10


def test_backfill_price_jump_does_not_count_as_rising_rent_in_the_prose():
    start = date(2026, 8, 1)
    rows = []
    for offset in range(120):
        day = start + timedelta(days=offset)
        rows.append(
            {
                "date_kst": day.isoformat(),
                "or_total_tokens": "1000",
                "vast_h100_median": str(3 - offset / 1000),
                "vast_h200_median": str(5 - offset / 1000),
                "vllm_last_day": "100",
            }
        )
    view = _view(date_kst="2026-11-15", or_total=1000, or_previous=1000, or_ma=1000)
    score = _score(("vast_h100", 1), ("vast_h200", 1), ("openrouter", 1), verdict="Strong")
    text = "\n".join(build_commentary(view, score, merge_history(rows, []), notes="vast: dod vs backfill"))
    assert "공급 확대가 수요 증가를 흡수" in text
    assert "수요 우위" not in text


def test_missing_sources_skip_their_sentences():
    view = DayView(
        date_kst="2026-11-15",
        spot_values={column: None for column in ("spot_use1_p5", "spot_use1_p4d", "spot_usw2_p5", "spot_usw2_p4d")},
        spot_labels={},
        spot_reason="요청 실패",
        vast={"h100": VastQuote(), "h200": VastQuote(), "b200": VastQuote()},
        vast_prev={},
        vast_reason="요청 실패",
        vllm_last_day=100,
        vllm_ma=80,
        vllm_reason="",
        pjm_mw=None,
        pjm_prev=None,
        pjm_ma=None,
        pjm_reason="EIA_API_KEY 없음",
        or_total=None,
        overall="Strong",
    )
    score = _score(("vllm", 1), verdict="Stable")
    text = "\n".join(build_commentary(view, score, {}))
    assert "1개 지표 중 강세 1 · 중립 0 · 약세 0" in text
    assert "vLLM 다운로드 7일 평균 상회" in text
    assert "Vast" not in text
    assert "OpenRouter" not in text
    assert "PJM" not in text
    assert "HF" not in text
    assert "Spot 중단률" not in text
    assert "📈 추이" not in text
    assert "🔄 주요 변동" not in text


def test_short_history_omits_30_and_90_day_trends():
    start = date(2026, 11, 6)
    rows = _series("vllm_last_day", start, 10, 100)
    view = DayView(
        date_kst="2026-11-15",
        spot_values={},
        spot_labels={},
        spot_reason="",
        vast={},
        vast_prev={},
        vast_reason="",
        vllm_last_day=100,
        vllm_ma=100,
        vllm_reason="",
        pjm_mw=None,
        pjm_prev=None,
        pjm_ma=None,
        pjm_reason="",
        overall="Stable",
    )
    text = "\n".join(build_commentary(view, Score(0, "Stable", ()), merge_history(rows, [])))
    assert "30일 전 대비" not in text
    assert "90일 전 대비" not in text
    assert "vLLM 7일평균" not in text
    assert moving_average({start + timedelta(days=i): 100.0 for i in range(10)}, date(2026, 11, 15)) == 100


def test_daily_history_wins_over_backfill_on_the_same_date():
    day = "2026-11-14"
    merged = merge_history(
        [{"date_kst": day, "vllm_last_day": "1", "or_total_tokens": "9"}],
        [{"date_kst": day, "vllm_last_day": "100", "vast_h100_median": "2"}],
    )
    assert merged[date(2026, 11, 14)]["vllm_last_day"] == "1"
    assert merged[date(2026, 11, 14)]["vast_h100_median"] == "2"
    assert merged[date(2026, 11, 14)]["or_total_tokens"] == "9"


def test_overlay_does_not_erase_a_live_gap_with_none():
    history = {date(2026, 11, 15): {"date_kst": "2026-11-15", "or_total_tokens": "50", "vllm_last_day": "7"}}
    view = DayView(
        date_kst="2026-11-15",
        spot_values={},
        spot_labels={},
        spot_reason="",
        vast={"h100": VastQuote(None, 3.5)},
        vast_prev={},
        vast_reason="",
        vllm_last_day=None,
        vllm_ma=None,
        vllm_reason="",
        pjm_mw=None,
        pjm_prev=None,
        pjm_ma=None,
        pjm_reason="",
        or_total=None,
        overall="Stable",
    )
    row = overlay_live(history, view)[date(2026, 11, 15)]
    assert row["or_total_tokens"] == "50"
    assert row["vllm_last_day"] == "7"
    assert row["vast_h100_median"] == "3.5"


def test_commentary_failure_does_not_drop_the_rest_of_the_message():
    view = DayView(
        date_kst="not-a-date",
        spot_values={},
        spot_labels={},
        spot_reason="",
        vast={},
        vast_prev={},
        vast_reason="",
        vllm_last_day=None,
        vllm_ma=None,
        vllm_reason="요청 실패",
        pjm_mw=None,
        pjm_prev=None,
        pjm_ma=None,
        pjm_reason="요청 실패",
        overall="Stable",
    )
    text = render(view, score=Score(0, "Stable", ()))
    assert "종합: Stable" in text
    assert "Data: Vast.ai" in text
    assert len(text) <= TELEGRAM_LIMIT


def test_fit_telegram_drops_commentary_before_the_sheet_link():
    lines = ["머리", "종합: Stable", "📝 긴 근거", "🔄 변동", "💡 해설: 문장", "📈 시트: https://example.test", "Data: Vast.ai"]
    text = fit_telegram(lines, limit=80)
    assert "📈 시트: https://example.test" in text
    assert len(text) <= 80
    assert text.index("종합") < text.index("📈 시트")
