import requests

from ai_demand.telegram import send_album, send_telegram


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


def test_send_album_uses_one_media_group(monkeypatch, tmp_path):
    paths = []
    for name in ("a.png", "b.png"):
        path = tmp_path / name
        path.write_bytes(b"png")
        paths.append(path)
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True}

    def fake_post(url, data, files, timeout, headers):
        captured["url"] = url
        captured["data"] = data
        captured["files"] = list(files)
        return Response()

    monkeypatch.setattr(requests, "post", fake_post)
    send_album([(paths[0], "OpenRouter 일일 토큰"), (paths[1], "Vast.ai 중간 가격")], "TOKEN", "123")
    assert captured["url"].endswith("/sendMediaGroup")
    assert "botTOKEN" in captured["url"]
    assert captured["files"] == ["chart0", "chart1"]
    assert "attach://chart0" in captured["data"]["media"]
    assert "OpenRouter 일일 토큰" in captured["data"]["media"]


def test_send_album_uses_send_photo_for_one_chart(monkeypatch, tmp_path):
    path = tmp_path / "only.png"
    path.write_bytes(b"png")
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True}

    def fake_post(url, data, files, timeout, headers):
        captured["url"] = url
        captured["data"] = data
        captured["name"] = next(iter(files))
        return Response()

    monkeypatch.setattr(requests, "post", fake_post)
    send_album([(path, "PJM 야간 기저부하")], "TOKEN", "9")
    assert captured["url"].endswith("/sendPhoto")
    assert captured["name"] == "photo"
    assert captured["data"]["caption"] == "PJM 야간 기저부하"
