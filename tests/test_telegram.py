import requests

from ai_demand.telegram import send_telegram


def test_send_telegram_is_plain_text(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True}

    def fake_post(url, json, timeout, headers):
        captured["url"] = url
        captured["json"] = json
        return Response()

    monkeypatch.setattr(requests, "post", fake_post)
    send_telegram("1단계 (<5%)", "TOKEN", "123")
    assert captured["json"]["text"] == "1단계 (<5%)"
    assert "parse_mode" not in captured["json"]
    assert captured["json"]["disable_web_page_preview"] is True
    assert captured["json"]["chat_id"] == "123"
    assert captured["url"].endswith("/sendMessage")
    assert "botTOKEN" in captured["url"]
