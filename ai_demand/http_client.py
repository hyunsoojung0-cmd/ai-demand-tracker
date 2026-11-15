"""GET JSON with a short timeout and a few retries.

Retries cover timeouts, connection errors, HTTP 429/5xx, and a truncated body.
A 4xx other than 429 fails immediately. The URL written to the log has no query
string, so an EIA key or bot token in the query is not recorded.
"""

import logging
import time

import requests

from ai_demand.config import HTTP_RETRIES, HTTP_RETRY_SLEEP_SECONDS, HTTP_TIMEOUT, USER_AGENT

log = logging.getLogger(__name__)


def _retryable(exc: Exception) -> bool:
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return True
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        status = exc.response.status_code
        return status == 429 or status >= 500
    if isinstance(exc, ValueError):
        return True
    return False


def get_json(
    url: str,
    params: list | dict | None = None,
    headers: dict | None = None,
    retries: int = HTTP_RETRIES,
    timeout: tuple[float, float] | float | None = None,
):
    """Return the parsed JSON body or raise the last error."""
    request_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        request_headers.update(headers)
    request_timeout = HTTP_TIMEOUT if timeout is None else timeout
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            response = requests.get(url, params=params, timeout=request_timeout, headers=request_headers)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            last = exc
            detail = type(exc).__name__
            response = getattr(exc, "response", None)
            if response is not None and getattr(response, "status_code", None):
                detail = f"{detail} {response.status_code}"
            # Path only. The query can carry an EIA key or other secret.
            log.warning(
                "GET %s attempt %s/%s failed: %s",
                url.split("?", 1)[0],
                attempt,
                retries,
                detail,
            )
            if attempt >= retries or not _retryable(exc):
                break
            time.sleep(HTTP_RETRY_SLEEP_SECONDS * attempt)
    assert last is not None
    raise last
