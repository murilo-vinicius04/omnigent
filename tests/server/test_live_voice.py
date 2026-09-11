"""Live voice: credential resolution, request shape, and the routes."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omnigent.server import live_voice
from omnigent.server.routes.live_voice import create_live_voice_router


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(create_live_voice_router(), prefix="/v1")
    return TestClient(app)


def test_key_prefers_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    monkeypatch.setattr(live_voice, "KEY_PATH", tmp_path / "openai-key")
    assert live_voice.api_key() == "sk-from-env"


def test_key_falls_back_to_the_file(monkeypatch, tmp_path):
    key_path = tmp_path / "openai-key"
    key_path.write_text("sk-from-file\n", encoding="utf-8")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(live_voice, "KEY_PATH", key_path)
    assert live_voice.api_key() == "sk-from-file"
    assert live_voice.available() is True


def test_missing_key_is_reported_not_guessed(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(live_voice, "KEY_PATH", tmp_path / "absent")
    assert live_voice.available() is False
    with pytest.raises(live_voice.LiveVoiceUnavailable):
        live_voice.api_key()


def test_session_config_uses_the_field_names_the_api_accepts():
    # The API rejects unknown keys outright, and a top-level `voice` is one
    # of them: the voice belongs under audio.output.
    config = live_voice.session_config(instructions="be brief", model=None, voice=None)
    assert config["model"] == live_voice.DEFAULT_MODEL
    assert config["instructions"] == "be brief"
    assert config["audio"]["output"]["voice"] == live_voice.DEFAULT_VOICE
    assert "voice" not in config


def test_offer_without_a_key_is_unavailable_not_a_crash(client, monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(live_voice, "KEY_PATH", tmp_path / "absent")
    response = client.post("/v1/live/offer", json={"sdp": "v=0"})
    assert response.status_code == 503


def test_offer_surfaces_openai_refusals(client, monkeypatch):
    # "No credits remaining" must reach the reader verbatim; a generic 500
    # would send them looking for a bug in our code instead of their billing.
    async def refuse(**_kwargs):
        request = httpx.Request("POST", live_voice.LIVE_SESSIONS_URL)
        response = httpx.Response(429, text="You have no credits remaining.", request=request)
        raise httpx.HTTPStatusError("429", request=request, response=response)

    monkeypatch.setattr(live_voice, "open_session", refuse)
    monkeypatch.setattr("omnigent.server.routes.live_voice.open_session", refuse)
    response = client.post("/v1/live/offer", json={"sdp": "v=0"})
    assert response.status_code == 502
    assert "no credits remaining" in response.json()["detail"]


def test_offer_returns_the_answer_sdp(client, monkeypatch):
    async def accept(*, sdp_offer, instructions, model=None, voice=None):
        assert sdp_offer == "v=0 offer"
        assert instructions
        return "live_abc", "v=0 answer"

    monkeypatch.setattr("omnigent.server.routes.live_voice.open_session", accept)
    response = client.post("/v1/live/offer", json={"sdp": "v=0 offer"})
    assert response.status_code == 200
    assert response.json() == {"session_id": "live_abc", "sdp": "v=0 answer"}


def test_test_page_bakes_in_the_rate_and_cap(client):
    body = client.get("/v1/live/test").text
    assert "Live voice probe" in body
    assert str(live_voice.MAX_SESSION_S) in body
    assert repr(float(live_voice.USD_PER_MINUTE)) in body
    # The page must never carry the key itself.
    assert "sk-" not in body


def test_page_closes_the_session_when_the_tab_goes_away(client):
    # The API has no terminate call, so a closed tab that leaves the peer
    # connection open would bill until the runaway cap.
    body = client.get("/v1/live/test").text
    assert "pagehide" in body
    assert "beforeunload" in body
