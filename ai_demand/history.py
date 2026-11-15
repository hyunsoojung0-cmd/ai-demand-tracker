"""Read and upsert ``data/history.csv``.

The header is part of the contract with the sheet copy that runs later each
morning. A mismatched header is refused rather than rewritten. Re-running a
date replaces that row in place.
"""

import csv
from pathlib import Path

from ai_demand.config import (
    AVERAGE_DECIMALS,
    HISTORY_COLUMNS,
    HISTORY_HEADER,
    PRICE_DECIMALS,
    SPOT_COLUMNS,
    VAST_DISPLAY,
)
from ai_demand.model import DayView

DEFAULT_CSV = Path("data/history.csv")


def format_number(value: float | int | None, places: int) -> str:
    """Fixed-precision decimal with trailing zeros removed. Blank when missing."""
    if value is None:
        return ""
    text = f"{float(value):.{places}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def format_int(value: int | None) -> str:
    if value is None:
        return ""
    return str(int(value))


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            return []
        if list(reader.fieldnames) != list(HISTORY_COLUMNS):
            raise ValueError(f"unexpected CSV header: {reader.fieldnames}")
        return [{column: (row.get(column) or "") for column in HISTORY_COLUMNS} for row in reader]


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(rows, key=lambda row: row["date_kst"])
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(HISTORY_COLUMNS), lineterminator="\n")
        writer.writeheader()
        for row in ordered:
            unknown = set(row) - set(HISTORY_COLUMNS)
            if unknown:
                raise ValueError(f"unknown columns: {sorted(unknown)}")
            writer.writerow({column: row.get(column, "") for column in HISTORY_COLUMNS})
    temporary.replace(path)


def upsert_rows(path: Path, new_rows: list[dict[str, str]]) -> None:
    """Insert or replace rows keyed by ``date_kst``. Other dates are kept."""
    by_date = {row["date_kst"]: row for row in read_rows(path)}
    for row in new_rows:
        if not row.get("date_kst"):
            raise ValueError("row is missing date_kst")
        by_date[row["date_kst"]] = {column: row.get(column, "") for column in HISTORY_COLUMNS}
    write_rows(path, list(by_date.values()))


def previous_row(rows: list[dict[str, str]], date_kst: str) -> dict[str, str] | None:
    earlier = [row for row in rows if row.get("date_kst", "") < date_kst]
    if not earlier:
        return None
    return max(earlier, key=lambda row: row["date_kst"])


def parse_optional_float(raw: str | None) -> float | None:
    text = (raw or "").strip()
    if not text:
        return None
    return float(text)


def previous_spots(row: dict[str, str] | None) -> list[int] | None:
    if row is None:
        return None
    values = []
    for column in SPOT_COLUMNS:
        text = (row.get(column) or "").strip()
        if not text:
            continue
        values.append(int(text))
    return values or None


def row_from_view(view: DayView, notes: str) -> dict[str, str]:
    row = {column: "" for column in HISTORY_COLUMNS}
    row["date_kst"] = view.date_kst
    for column in SPOT_COLUMNS:
        row[column] = format_int(view.spot_values.get(column))
    for key, _display in VAST_DISPLAY:
        quote = view.vast.get(key)
        row[f"vast_{key}_min"] = format_number(None if quote is None else quote.min_price, PRICE_DECIMALS)
        row[f"vast_{key}_median"] = format_number(None if quote is None else quote.median, PRICE_DECIMALS)
    row["vllm_last_day"] = format_int(view.vllm_last_day)
    row["vllm_7d_ma"] = format_number(view.vllm_ma, AVERAGE_DECIMALS)
    row["pjm_overnight_mw"] = format_number(view.pjm_mw, AVERAGE_DECIMALS)
    row["pjm_overnight_7d_ma"] = format_number(view.pjm_ma, AVERAGE_DECIMALS)
    row["overall"] = view.overall
    row["notes"] = notes
    return row


def assert_header_contract() -> None:
    """Imported by tests. Keeps the on-disk header identical to the spec."""
    if HISTORY_HEADER != (
        "date_kst,spot_use1_p5,spot_use1_p4d,spot_usw2_p5,spot_usw2_p4d,"
        "vast_h100_min,vast_h100_median,vast_h200_min,vast_h200_median,"
        "vast_b200_min,vast_b200_median,vllm_last_day,vllm_7d_ma,"
        "pjm_overnight_mw,pjm_overnight_7d_ma,overall,notes"
    ):
        raise RuntimeError("history header drifted from the sheet contract")
