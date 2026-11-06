"""Groq client: model fallback, empty-answer handling, reasoning-model settings. No network (httpx is stubbed)."""
from backend import config, llm


class FakeResp:
    def __init__(self, content, status=200):
        self._c, self.status_code = content, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return {"choices": [{"message": {"content": self._c}}]}


def test_disabled_without_key(monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    assert not llm.enabled() and llm.chat("hi") is None


def test_falls_back_when_primary_returns_empty(monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "k")
    seen = []

    def fake_post(url, json, headers, timeout):
        seen.append(json)
        return FakeResp("" if json["model"] == config.GROQ_MODEL else '{"employees": 4}')

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    assert llm.chat_json("x") == {"employees": 4}
    assert [b["model"] for b in seen] == [config.GROQ_MODEL, config.GROQ_FALLBACK_MODEL]
    assert seen[0].get("reasoning_effort") == "low" and seen[0]["max_tokens"] >= 1500
    assert "reasoning_effort" not in seen[1]


def test_all_models_failing_returns_none(monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "k")
    monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: FakeResp("", 500))
    assert llm.chat("x") is None and llm.chat_json("x") is None


def test_bad_json_is_safe(monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "k")
    monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: FakeResp("not json at all"))
    assert llm.chat_json("x") is None