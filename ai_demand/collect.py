"""Collect the four indicators, append today's row, and print or send the summary.

One source failing is a warning. The process exits 0 unless every source failed
or the CSV could not be written. ``--dry-run`` prints the message and does not
write the CSV or call Telegram. Missing ``BOT_TOKEN`` or ``CHAT_ID`` also skips
the send, and still writes the CSV when this is not a dry run.
"""

import argparse
import logging
import os
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from ai_demand.backfill import backfill
from ai_demand.backfill6m import BACKFILL_6M_CSV
from ai_demand.commentary import merge_history
from ai_demand.config import SPOT_COLUMNS, SPOT_INSTANCES, VAST_DISPLAY
from ai_demand.errors import SourceError
from ai_demand.huggingface import fetch_huggingface, implied_download, implied_moving_average
from ai_demand.openrouter import fetch_openrouter
from ai_demand.history import (
    DEFAULT_CSV,
    parse_optional_float,
    previous_row,
    previous_spots,
    read_rows,
    row_from_view,
    row_on_or_before,
    spot_bucket,
    upsert_rows,
)
from ai_demand.message import render
from ai_demand.model import DayView, VastQuote
from ai_demand.pjm import fetch_pjm
from ai_demand.pypi_stats import fetch_pypi
from ai_demand.score import score_view
from ai_demand.spot import fetch_spot
from ai_demand.telegram import send_telegram
from ai_demand.vast import fetch_vast

log = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")


def kst_today() -> date:
    return datetime.now(KST).date()


def failure_reason_ko(exc: Exception | None) -> str:
    if isinstance(exc, SourceError):
        return {
            "missing_key": "EIA_API_KEY 없음",
            "missing_openrouter_key": "OPENROUTER_API_KEY 없음",
            "request_failed": "요청 실패",
            "not_in_response": "응답에 없음",
            "incomplete": "완전한 야간 구간 없음",
            "no_complete_day": "완전한 일자 없음",
        }.get(exc.code, "요청 실패")
    if exc is None:
        return "응답에 없음"
    return "요청 실패"


def failure_note(source: str, exc: Exception) -> str:
    if isinstance(exc, SourceError):
        detail = {
            "missing_key": "EIA_API_KEY missing",
            "missing_openrouter_key": "OPENROUTER_API_KEY missing",
            "request_failed": "request failed",
            "not_in_response": "not in response",
            "incomplete": "no complete night",
            "no_complete_day": "no complete day",
        }.get(exc.code, "request failed")
        return f"{source}: {detail}"
    return f"{source}: request failed"


def build_notes(
    *,
    spot_failure: str | None = None,
    spot_partial: bool = False,
    vast_failure: str | None = None,
    vast_fallback: bool = False,
    vast_vs_backfill: bool = False,
    vllm_failure: str | None = None,
    vllm_ma_missing: bool = False,
    vllm_mismatch: bool = False,
    pjm_failure: str | None = None,
    pjm_latest_incomplete: bool = False,
    pjm_prior_gap: bool = False,
    openrouter_failure: str | None = None,
    hf_failure: str | None = None,
    hf_partial: str | None = None,
) -> str:
    """Stable, semicolon-separated notes. Routine success leaves this empty."""
    notes: list[str] = []
    if spot_failure:
        notes.append(spot_failure)
    elif spot_partial:
        notes.append("spot: partial")
    if vast_failure:
        notes.append(vast_failure)
    else:
        if vast_fallback:
            notes.append("vast: bundles fallback")
        if vast_vs_backfill:
            notes.append("vast: dod vs backfill")
    if vllm_failure:
        notes.append(vllm_failure)
    else:
        if vllm_ma_missing:
            notes.append("vllm: 7d ma unavailable")
        if vllm_mismatch:
            notes.append("vllm: last_day mismatch")
    if pjm_failure:
        notes.append(pjm_failure)
    else:
        if pjm_latest_incomplete:
            notes.append("pjm: latest night incomplete")
        if pjm_prior_gap:
            notes.append("pjm: prior gap")
    if openrouter_failure:
        notes.append(openrouter_failure)
    if hf_failure:
        notes.append(hf_failure)
    elif hf_partial:
        notes.append(hf_partial)
    return "; ".join(notes)


def _capture(name: str, func):
    try:
        return func(), None
    except Exception as exc:
        log.warning("%s unavailable: %s", name, failure_note(name, exc))
        return None, exc


def _implied_history(rows: list[dict[str, str]], today_text: str) -> dict[date, int]:
    found: dict[date, int] = {}
    for row in rows:
        if row.get("date_kst", "") >= today_text:
            continue
        raw = (row.get("hf_implied_daily") or "").strip()
        if not raw:
            continue
        found[date.fromisoformat(row["date_kst"])] = int(raw)
    return found


def _spot_history(row: dict | None) -> dict[str, int | None]:
    return {target.column: spot_bucket(row, target.column) for target in SPOT_INSTANCES}


def _build_view(
    today: str,
    previous: dict | None,
    week: dict | None,
    spot,
    spot_exc,
    vast,
    vast_exc,
    pypi,
    pypi_exc,
    pjm,
    pjm_exc,
) -> DayView:
    if spot is None:
        spot_values = {target.column: None for target in SPOT_INSTANCES}
        spot_labels: dict[str, str] = {}
        spot_reason = failure_reason_ko(spot_exc)
    else:
        spot_values = {target.column: spot.values.get(target.column) for target in SPOT_INSTANCES}
        spot_labels = dict(spot.labels)
        spot_reason = "응답에 없음"

    if vast is None:
        quotes = {key: VastQuote() for key, _display in VAST_DISPLAY}
        vast_reason = failure_reason_ko(vast_exc)
    else:
        quotes = {key: vast.quotes.get(key, VastQuote()) for key, _display in VAST_DISPLAY}
        vast_reason = "응답에 없음"

    vast_prev = {}
    for key, _display in VAST_DISPLAY:
        raw = None if previous is None else previous.get(f"vast_{key}_median")
        vast_prev[key] = parse_optional_float(raw)

    if pypi is None:
        last_day = None
        vllm_ma = None
        vllm_reason = failure_reason_ko(pypi_exc)
    else:
        last_day = pypi.last_day
        vllm_ma = pypi.ma_7d
        vllm_reason = "요청 실패"

    if pjm is None:
        pjm_mw = pjm_prev = pjm_ma = None
        pjm_reason = failure_reason_ko(pjm_exc)
    else:
        pjm_mw = pjm.overnight_mw
        pjm_prev = pjm.previous_mw
        pjm_ma = pjm.ma_7d
        pjm_reason = "데이터 없음"

    return DayView(
        date_kst=today,
        spot_values=spot_values,
        spot_labels=spot_labels,
        spot_reason=spot_reason,
        vast=quotes,
        vast_prev=vast_prev,
        vast_reason=vast_reason,
        vllm_last_day=last_day,
        vllm_ma=vllm_ma,
        vllm_reason=vllm_reason,
        pjm_mw=pjm_mw,
        pjm_prev=pjm_prev,
        pjm_ma=pjm_ma,
        pjm_reason=pjm_reason,
        spot_prev=_spot_history(previous),
        spot_week=_spot_history(week),
    )


def _maybe_send(text: str, dry_run: bool) -> None:
    token = os.environ.get("BOT_TOKEN", "").strip()
    chat_id = os.environ.get("CHAT_ID", "").strip()
    if dry_run or not token or not chat_id:
        log.info("telegram skipped (dry_run or missing BOT_TOKEN/CHAT_ID)")
        return
    try:
        send_telegram(text, token, chat_id)
        log.info("telegram sent")
    except Exception as exc:
        log.warning("telegram failed: %s", type(exc).__name__)


def collect(path: Path = DEFAULT_CSV, dry_run: bool = False, today: date | None = None) -> int:
    today_text = (today or kst_today()).isoformat()
    spot, spot_exc = _capture("spot", fetch_spot)
    vast, vast_exc = _capture("vast", fetch_vast)
    pypi, pypi_exc = _capture("vllm", fetch_pypi)
    pjm, pjm_exc = _capture("pjm", fetch_pjm)
    openrouter, or_exc = _capture("openrouter", fetch_openrouter)
    huggingface, hf_exc = _capture("hf", fetch_huggingface)

    try:
        rows = read_rows(path)
    except Exception as exc:
        log.error("csv read failed: %s", type(exc).__name__)
        return 1
    previous = previous_row(rows, today_text)
    week_day = (date.fromisoformat(today_text) - timedelta(days=7)).isoformat()
    week = row_on_or_before(rows, week_day)
    view = _build_view(today_text, previous, week, spot, spot_exc, vast, vast_exc, pypi, pypi_exc, pjm, pjm_exc)
    today_date = date.fromisoformat(today_text)
    if openrouter is None:
        view = replace(view, or_reason=failure_reason_ko(or_exc))
    else:
        view = replace(
            view,
            or_total=openrouter.total_tokens,
            or_previous=openrouter.previous_tokens,
            or_ma=openrouter.ma_7d,
            or_top5_share=openrouter.top5_share,
            or_top1=openrouter.top1_model,
            or_date=openrouter.day,
        )
    previous_alltime = None
    if previous is not None and (previous.get("hf_basket_alltime") or "").strip():
        previous_alltime = int(previous["hf_basket_alltime"])
    if huggingface is None:
        view = replace(view, hf_reason=failure_reason_ko(hf_exc))
    else:
        implied = None if huggingface.failed else implied_download(huggingface.alltime, previous_alltime)
        view = replace(
            view,
            hf_30d=huggingface.downloads_30d,
            hf_alltime=huggingface.alltime,
            hf_implied=implied,
            hf_implied_ma=implied_moving_average(_implied_history(rows, today_text), today_date, implied),
            hf_failed=huggingface.failed,
            hf_basket_size=huggingface.basket_size,
        )
    # Spot score uses the same previous row as the ↑/↓ arrow: the average of its r values.
    scored = score_view(view, previous_spots(previous))
    view = replace(view, overall=scored.verdict)
    log.info(
        "score total=%s verdict=%s signals=%s",
        scored.total,
        scored.verdict,
        ",".join(f"{part.name}:{part.value:+d}" for part in scored.signals) or "none",
    )

    previous_notes = [] if previous is None else [part.strip() for part in (previous.get("notes") or "").split(";")]
    previous_has_median = previous is not None and any(
        parse_optional_float(previous.get(f"vast_{key}_median")) is not None for key, _display in VAST_DISPLAY
    )
    notes = build_notes(
        spot_failure=None if spot_exc is None else failure_note("spot", spot_exc),
        spot_partial=spot is not None and any(view.spot_values[column] is None for column in SPOT_COLUMNS),
        vast_failure=None if vast_exc is None else failure_note("vast", vast_exc),
        vast_fallback=bool(vast is not None and vast.used_fallback),
        vast_vs_backfill=bool(vast is not None and previous_has_median and "backfill" in previous_notes),
        vllm_failure=None if pypi_exc is None else failure_note("vllm", pypi_exc),
        vllm_ma_missing=pypi is not None and pypi.ma_7d is None,
        vllm_mismatch=bool(
            pypi is not None and pypi.latest_overall is not None and pypi.latest_overall != pypi.last_day
        ),
        pjm_failure=None if pjm_exc is None else failure_note("pjm", pjm_exc),
        pjm_latest_incomplete=bool(pjm is not None and pjm.latest_incomplete),
        pjm_prior_gap=bool(pjm is not None and pjm.prior_gap),
        openrouter_failure=None if or_exc is None else failure_note("openrouter", or_exc),
        hf_failure=None if hf_exc is None else failure_note("hf", hf_exc),
        hf_partial=(
            None
            if huggingface is None or huggingface.failed == 0
            else f"hf: {huggingface.failed}/{huggingface.basket_size} models failed"
        ),
    )
    backfill_rows: list[dict[str, str]] = []
    if BACKFILL_6M_CSV.exists():
        try:
            backfill_rows = read_rows(BACKFILL_6M_CSV)
        except Exception as exc:
            log.warning("backfill file skipped: %s", type(exc).__name__)
    message = render(view, score=scored, history=merge_history(rows, backfill_rows), notes=notes)
    print(message)

    any_ok = any(item is not None for item in (spot, vast, pypi, pjm, openrouter, huggingface))
    if dry_run:
        log.info("dry_run: skip csv write and telegram")
        return 0 if any_ok else 1
    try:
        upsert_rows(path, [row_from_view(view, notes)])
    except Exception as exc:
        log.error("csv write failed: %s", type(exc).__name__)
        return 1
    _maybe_send(message, dry_run=False)
    return 0 if any_ok else 1


def _parse_date(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"date must be YYYY-MM-DD ({exc})") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect daily AI-infrastructure demand indicators.")
    parser.add_argument("--dry-run", action="store_true", help="Print the message. Do not write the CSV or send Telegram.")
    parser.add_argument("--backfill", action="store_true", help="Seed 2026-11-02..2026-11-14 and exit.")
    parser.add_argument("--date", type=_parse_date, default=None, help="Override the KST row date (YYYY-MM-DD).")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="Path to history.csv.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.backfill:
        return backfill(args.csv)
    return collect(path=args.csv, dry_run=args.dry_run, today=args.date)
