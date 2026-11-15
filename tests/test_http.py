import requests

from ai_demand.http_client import get_json


def test_get_json_retries_timeouts(monkeypatch):
    calls = {"n": 0}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True}

    def fake_get(url, params=None, timeout=None, headers=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise requests.Timeout("slow")
        return Response()

    monkeypatch.setattr("ai_demand.http_client.requests.get", fake_get)
    monkeypatch.setattr("ai_demand.http_client.time.sleep", lambda _seconds: None)
    assert get_json("https://example.test/feed?secret=1") == {"ok": True}
    assert calls["n"] == 3


def test_get_json_does_not_retry_not_found(monkeypatch):
    calls = {"n": 0}

    def fake_get(url, params=None, timeout=None, headers=None):
        calls["n"] += 1
        response = requests.Response()
        response.status_code = 404
        raise requests.HTTPError("missing", response=response)

    monkeypatch.setattr("ai_demand.http_client.requests.get", fake_get)
    monkeypatch.setattr("ai_demand.http_client.time.sleep", lambda _seconds: None)
    try:
        get_json("https://example.test/missing")
    except requests.HTTPError:
        pass
    else:
        raise AssertionError("404 should raise")
    assert calls["n"] == 1
