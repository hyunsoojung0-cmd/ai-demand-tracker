import pytest


@pytest.fixture(autouse=True)
def _clear_secrets(monkeypatch):
    for name in ("BOT_TOKEN", "CHAT_ID", "EIA_API_KEY"):
        monkeypatch.delenv(name, raising=False)
