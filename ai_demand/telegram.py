"""Send the summary as plain text, then an album of chart PNGs.

No parse mode, so ``<5%`` is not markup. The bot token stays out of the log.
"""

import json
import logging
import time
from pathlib import Path

import requests

from ai_demand.config import HTTP_RETRIES, HTTP_RETRY_SLEEP_SECONDS, HTTP_TIMEOUT, USER_AGENT
from ai_demand.errors import SourceError

log = logging.getLogger(__name__)


def send_telegram(text: str, token: str, chat_id: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    headers = {"User-Agent": USER_AGENT}
    last_type = "error"
    for attempt in range(1, HTTP_RETRIES + 1):
        try:
            response = requests.post(url, json=payload, timeout=HTTP_TIMEOUT, headers=headers)
            response.raise_for_status()
            body = response.json()
            if not body.get("ok", False):
                raise RuntimeError("telegram api not ok")
            return
        except Exception as exc:
            # The request URL contains the bot token. Log only the exception type.
            last_type = type(exc).__name__
            log.warning("telegram attempt %s/%s failed: %s", attempt, HTTP_RETRIES, last_type)
            if attempt < HTTP_RETRIES:
                time.sleep(HTTP_RETRY_SLEEP_SECONDS * attempt)
    raise SourceError("request_failed") from None


def send_album(items: list[tuple[Path, str]], token: str, chat_id: str) -> None:
    """Send one photo, or one album when there are two or more.

    ``sendMediaGroup`` rejects a single item, so one chart uses ``sendPhoto``.
    More than ten charts are split. Callers keep the list at the five dashboard
    charts.
    """
    if not items:
        return
    pending = list(items)
    while pending:
        chunk = pending[:10]
        del pending[:10]
        if len(chunk) == 1:
            _send_photo(chunk[0][0], chunk[0][1], token, chat_id)
        else:
            _send_media_group(chunk, token, chat_id)


def _send_photo(path: Path, caption: str, token: str, chat_id: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendPhoto"

    def attempt() -> None:
        with path.open("rb") as handle:
            data = {"chat_id": chat_id}
            if caption:
                data["caption"] = caption
            _post_files(url, data=data, files={"photo": handle})

    _retry(attempt)


def _send_media_group(items: list[tuple[Path, str]], token: str, chat_id: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMediaGroup"

    def attempt() -> None:
        handles = []
        try:
            files = {}
            media = []
            for index, (path, caption) in enumerate(items):
                key = f"chart{index}"
                handle = path.open("rb")
                handles.append(handle)
                files[key] = handle
                entry = {"type": "photo", "media": f"attach://{key}"}
                if caption:
                    entry["caption"] = caption
                media.append(entry)
            data = {"chat_id": chat_id, "media": json.dumps(media, ensure_ascii=False)}
            _post_files(url, data=data, files=files)
        finally:
            for handle in handles:
                handle.close()

    _retry(attempt)


def _post_files(url: str, *, data: dict, files: dict) -> None:
    response = requests.post(
        url,
        data=data,
        files=files,
        timeout=(10, 60),
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    body = response.json()
    if not body.get("ok", False):
        raise RuntimeError("telegram api not ok")


def _retry(attempt) -> None:
    for number in range(1, HTTP_RETRIES + 1):
        try:
            attempt()
            return
        except Exception as exc:
            log.warning("telegram attempt %s/%s failed: %s", number, HTTP_RETRIES, type(exc).__name__)
            if number < HTTP_RETRIES:
                time.sleep(HTTP_RETRY_SLEEP_SECONDS * number)
    raise SourceError("request_failed") from None
