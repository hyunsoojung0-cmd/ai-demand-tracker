"""Send the summary as plain text. No parse mode, so ``<5%`` is not markup."""

import logging
import time

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
