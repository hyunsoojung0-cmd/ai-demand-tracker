import logging
import tempfile
from datetime import date
from pathlib import Path

from ai_demand.collect import already_collected, build_notes, collect, main
from ai_demand.config import HISTORY_COLUMNS
from ai_demand.errors import SourceError
from ai_demand.history import read_rows, upsert_rows
from ai_demand.model import VastQuote
from ai_demand.pypi_stats import PyPIReading
from ai_demand.spot import SpotReading
from ai_demand.vast import VastFetch


def _blank(day, **overrides):
    row = {column: "" for column in HISTORY_COLUMNS}
    row["date_kst"] = day
    row.update(overrides)
    return row


def _patch_success(monkeypatch):
    monkeypatch.setattr("ai_demand.collect.BACKFILL_6M_CSV", Path("/tmp/ai-demand-no-backfill.csv"))
    monkeypatch.setattr(
        "ai_demand.collect.fetch_spot",
        lambda: SpotReading(
            values={
                "spot_use1_p5": 0,
                "spot_use1_p4d": 3,
                "spot_usw2_p5": 0,
                "spot_usw2_p4d": 4,
            },
            labels={
                "spot_use1_p5": "<5%",
                "spot_use1_p4d": "15-20%",
                "spot_usw2_p5": "<5%",
                "spot_usw2_p4d": ">20%",
            },
        ),
    )
    monkeypatch.setattr(
        "ai_demand.collect.fetch_vast",
        lambda: VastFetch(
            quotes={
                "h100": VastQuote(1.6669, 2.667),
                "h200": VastQuote(3.5531, 5.1919),
                "b200": VastQuote(7.5013, 10.3132),
            },
            used_fallback=False,
        ),
    )
    monkeypatch.setattr(
        "ai_demand.collect.fetch_pypi",
        lambda: PyPIReading(last_day=73713, ma_7d=578209 / 7, latest_overall=73713),
    )

    def _pjm():
        raise SourceError("missing_key")

    monkeypatch.setattr("ai_demand.collect.fetch_pjm", _pjm)

    def _openrouter():
        raise SourceError("missing_openrouter_key")

    def _huggingface():
        raise SourceError("request_failed")

    monkeypatch.setattr("ai_demand.collect.fetch_openrouter", _openrouter)
    monkeypatch.setattr("ai_demand.collect.fetch_huggingface", _huggingface)
    monkeypatch.setattr(
        "ai_demand.collect.fetch_vast_availability",
        lambda: {"h100": 13, "h200": 14, "b200": 10},
    )
    monkeypatch.setenv("CHART_DIR", tempfile.mkdtemp(prefix="ai-demand-charts-"))


def test_notes_keep_a_stable_source_order():
    assert build_notes(
        spot_failure="spot: request failed",
        vast_fallback=True,
        vast_vs_backfill=True,
        vllm_ma_missing=True,
        pjm_failure="pjm: EIA_API_KEY missing",
    ) == (
        "spot: request failed; vast: bundles fallback; vast: dod vs backfill; "
        "vllm: 7d ma unavailable; pjm: EIA_API_KEY missing"
    )


def test_collect_replaces_today_and_keeps_the_backfill_row(tmp_path, monkeypatch, capsys):
    _patch_success(monkeypatch)
    path = tmp_path / "history.csv"
    upsert_rows(
        path,
        [
            _blank(
                "2026-11-14",
                vast_h100_median="2",
                vast_h200_median="3.5",
                vast_b200_median="8",
                vllm_last_day="73713",
                vllm_7d_ma="80000",
                notes="backfill",
            )
        ],
    )
    assert collect(path, today=date(2026, 11, 15)) == 0
    message = capsys.readouterr().out
    assert "us-east-1 p5 (H100): 1단계 (<5%)" in message
    assert "7일전" not in message
    assert "↑" not in message
    assert "H100 $2.67 (Δ +33.3%)" in message
    assert "H100 13대" in message
    assert "vLLM" not in message
    assert "Hugging Face" not in message
    assert "데이터 없음 (EIA_API_KEY 없음)" in message
    assert "종합: Stable" in message
    assert "Data: Vast.ai" in message
    assert message.strip().endswith("Source: OpenRouter (CC BY 4.0)")
    assert "데이터 없음 (OPENROUTER_API_KEY 없음)" in message

    assert collect(path, today=date(2026, 11, 15)) == 0
    rows = read_rows(path)
    assert [row["date_kst"] for row in rows] == ["2026-11-14", "2026-11-15"]
    assert rows[0]["notes"] == "backfill"
    today = rows[1]
    assert today["spot_use1_p5"] == "0"
    assert today["spot_use1_p4d"] == "3"
    assert today["spot_usw2_p5"] == "0"
    assert today["spot_usw2_p4d"] == "4"
    assert today["vast_h100_median"] == "2.667"
    assert today["vllm_last_day"] == "73713"
    assert today["vllm_7d_ma"] == "82601.29"
    assert today["pjm_overnight_mw"] == ""
    assert today["overall"] == "Stable"
    assert today["notes"] == (
        "vast: dod vs backfill; pjm: EIA_API_KEY missing; "
        "openrouter: OPENROUTER_API_KEY missing; hf: request failed"
    )
    assert today["or_total_tokens"] == ""
    assert today["hf_basket_alltime"] == ""
    assert today["vast_h100_avail"] == "13"
    assert today["vast_h200_avail"] == "14"
    assert today["vast_b200_avail"] == "10"


def test_collect_message_compares_each_spot_with_prior_day_and_week(tmp_path, monkeypatch, capsys):
    _patch_success(monkeypatch)
    path = tmp_path / "history.csv"
    upsert_rows(
        path,
        [
            _blank(
                "2026-11-08",
                spot_use1_p5="0",
                spot_use1_p4d="1",
                spot_usw2_p5="0",
                spot_usw2_p4d="2",
                notes="older",
            ),
            _blank(
                "2026-11-14",
                spot_use1_p5="0",
                spot_use1_p4d="2",
                spot_usw2_p5="1",
                spot_usw2_p4d="4",
                vast_h100_median="2",
                vast_h200_median="3.5",
                vast_b200_median="8",
                notes="yesterday",
            ),
        ],
    )
    assert collect(path, today=date(2026, 11, 15)) == 0
    message = capsys.readouterr().out
    assert "us-east-1 p5 (H100): 1단계 (<5%) → (전일 동일, 7일전 1단계)" in message
    assert "us-east-1 p4d (A100): 4단계 (15-20%) ↑ (전일 3단계, 7일전 2단계)" in message
    assert "us-west-2 p5 (H100): 1단계 (<5%) ↓ (전일 2단계, 7일전 1단계)" in message
    assert "us-west-2 p4d (A100): 5단계 (>20%) → (전일 동일, 7일전 3단계)" in message
    # Per-line arrows disagree, but the four-bucket average is unchanged (1.75), so Spot stays 0.
    assert "종합: Stable" in message
    rows = read_rows(path)
    assert [row["date_kst"] for row in rows] == ["2026-11-08", "2026-11-14", "2026-11-15"]
    assert rows[-1]["spot_use1_p4d"] == "3"
    assert "spot_week" not in rows[-1]


def test_dry_run_prints_and_does_not_touch_the_csv(tmp_path, monkeypatch, capsys):
    _patch_success(monkeypatch)
    path = tmp_path / "history.csv"
    upsert_rows(path, [_blank("2026-11-14", notes="backfill", vast_h100_median="2")])
    before = path.read_bytes()
    assert collect(path, dry_run=True, today=date(2026, 11, 15)) == 0
    assert path.read_bytes() == before
    assert "종합: Stable" in capsys.readouterr().out


def test_cli_dry_run_does_not_create_a_file(tmp_path, monkeypatch, capsys):
    _patch_success(monkeypatch)
    path = tmp_path / "missing.csv"
    code = main(["--dry-run", "--date", "2026-11-15", "--csv", str(path)])
    assert code == 0
    assert not path.exists()
    assert "[AI Demand Daily] 2026-11-15" in capsys.readouterr().out


def test_all_sources_failing_exits_nonzero(tmp_path, monkeypatch, capsys):
    def boom():
        raise SourceError("request_failed")

    for name in (
        "fetch_spot",
        "fetch_vast",
        "fetch_vast_availability",
        "fetch_pypi",
        "fetch_pjm",
        "fetch_openrouter",
        "fetch_huggingface",
    ):
        monkeypatch.setattr(f"ai_demand.collect.{name}", boom)
    path = tmp_path / "history.csv"
    assert collect(path, today=date(2026, 11, 15)) == 1
    row = read_rows(path)[0]
    assert row["overall"] == "Stable"
    assert row["spot_use1_p5"] == ""
    assert row["notes"] == (
        "spot: request failed; vast: request failed; vast_avail: request failed; "
        "vllm: request failed; pjm: request failed; openrouter: request failed; hf: request failed"
    )
    assert "데이터 없음 (요청 실패)" in capsys.readouterr().out


def test_chart_failure_still_sends_the_text(tmp_path, monkeypatch, caplog):
    _patch_success(monkeypatch)
    monkeypatch.setenv("BOT_TOKEN", "token")
    monkeypatch.setenv("CHAT_ID", "chat")
    sent = {}
    monkeypatch.setattr(
        "ai_demand.collect.send_telegram",
        lambda text, token, chat: sent.setdefault("text", text),
    )

    def boom(*_args, **_kwargs):
        raise RuntimeError("chart broke")

    monkeypatch.setattr("ai_demand.charts.render_charts", boom)
    path = tmp_path / "history.csv"
    with caplog.at_level(logging.INFO):
        assert collect(path, today=date(2026, 11, 15)) == 0
    assert "종합" in sent["text"]
    assert "charts failed" in caplog.text


def test_dry_run_saves_chart_files_without_sending(tmp_path, monkeypatch):
    _patch_success(monkeypatch)
    chart_path = tmp_path / "charts"
    monkeypatch.setenv("CHART_DIR", str(chart_path))
    monkeypatch.setenv("BOT_TOKEN", "token")
    monkeypatch.setenv("CHAT_ID", "chat")

    def _no_send(*_args, **_kwargs):
        raise AssertionError("telegram sent")

    monkeypatch.setattr("ai_demand.collect.send_telegram", _no_send)
    monkeypatch.setattr("ai_demand.telegram.send_album", _no_send)
    path = tmp_path / "history.csv"
    upsert_rows(path, [_blank("2026-11-14", vast_h100_median="2", notes="backfill")])
    before = path.read_bytes()
    assert collect(path, dry_run=True, today=date(2026, 11, 15)) == 0
    assert path.read_bytes() == before
    assert any(chart_path.glob("*.png"))


def test_already_collected_matches_only_todays_kst_row(tmp_path):
    missing = tmp_path / "missing.csv"
    assert already_collected(missing, "2026-11-15") is False
    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="utf-8")
    assert already_collected(empty, "2026-11-15") is False
    broken = tmp_path / "broken.csv"
    broken.write_text("not,the,header\n1,2,3\n", encoding="utf-8")
    assert already_collected(broken, "2026-11-15") is False
    path = tmp_path / "history.csv"
    upsert_rows(path, [_blank("2026-11-14", notes="yesterday")])
    assert already_collected(path, "2026-11-15") is False
    assert already_collected(path, "") is False
    upsert_rows(path, [_blank("2026-11-15", notes="today")])
    assert already_collected(path, "2026-11-15") is True
    assert already_collected(path, " 2026-11-15 ") is True


def test_fallback_skips_collection_and_telegram_when_today_exists(tmp_path, monkeypatch, caplog):
    def boom():
        raise AssertionError("source fetched")

    for name in (
        "fetch_spot",
        "fetch_vast",
        "fetch_vast_availability",
        "fetch_pypi",
        "fetch_pjm",
        "fetch_openrouter",
        "fetch_huggingface",
    ):
        monkeypatch.setattr(f"ai_demand.collect.{name}", boom)

    def _no_charts(*_args, **_kwargs):
        raise AssertionError("charts published")

    monkeypatch.setattr("ai_demand.collect.publish_charts", _no_charts)

    def _no_send(*_args, **_kwargs):
        raise AssertionError("telegram sent")

    monkeypatch.setattr("ai_demand.collect.send_telegram", _no_send)
    monkeypatch.setenv("BOT_TOKEN", "token")
    monkeypatch.setenv("CHAT_ID", "chat")
    path = tmp_path / "history.csv"
    upsert_rows(path, [_blank("2026-11-15", overall="Stable", notes="kept")])
    before = path.read_bytes()
    with caplog.at_level(logging.INFO):
        code = main(["--fallback", "--date", "2026-11-15", "--csv", str(path)])
    assert code == 0
    assert "already collected, skipping" in caplog.text
    assert path.read_bytes() == before


def test_fallback_collects_when_today_is_missing(tmp_path, monkeypatch, capsys):
    _patch_success(monkeypatch)
    path = tmp_path / "history.csv"
    upsert_rows(path, [_blank("2026-11-14", notes="backfill", vast_h100_median="2")])
    code = main(["--fallback", "--date", "2026-11-15", "--csv", str(path)])
    assert code == 0
    rows = read_rows(path)
    assert [row["date_kst"] for row in rows] == ["2026-11-14", "2026-11-15"]
    assert "[AI Demand Daily] 2026-11-15" in capsys.readouterr().out


def test_primary_and_manual_still_collect_when_today_exists(tmp_path, monkeypatch, capsys):
    _patch_success(monkeypatch)
    path = tmp_path / "history.csv"
    upsert_rows(path, [_blank("2026-11-15", notes="stale", vast_h100_median="1")])
    code = main(["--date", "2026-11-15", "--csv", str(path)])
    assert code == 0
    assert read_rows(path)[-1]["notes"] != "stale"
    assert "종합:" in capsys.readouterr().out

    upsert_rows(path, [_blank("2026-11-15", notes="kept")])
    before = path.read_bytes()
    code = main(["--dry-run", "--date", "2026-11-15", "--csv", str(path)])
    assert code == 0
    assert path.read_bytes() == before
    assert "[AI Demand Daily] 2026-11-15" in capsys.readouterr().out


def test_daily_workflow_checks_the_fallback_cron_before_collecting():
    text = Path(".github/workflows/daily.yml").read_text(encoding="utf-8")
    assert 'cron: "45 22 * * *"' in text
    assert 'cron: "15 0 * * *"' in text
    assert "group: ai-demand-daily" in text
    assert "cancel-in-progress: false" in text
    assert "ref: main" in text
    assert 'github.event.schedule' in text
    assert '"15 0 * * *"' in text
    assert "--fallback" in text
    assert "fonts-nanum" in text
    assert "upload-artifact" in text
    assert "daily-charts" in text
