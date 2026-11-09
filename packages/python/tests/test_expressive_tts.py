import base64
import json
from unittest.mock import AsyncMock

import pytest

from jev.providers import elevenlabs
from jev.prompts import EXPRESSIVE_VOICE_INSTRUCTIONS


def test_flash_remains_the_sdk_default():
    assert elevenlabs.tts().model == "eleven_flash_v2_5"


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["eleven_v4_turbo", "eleven_v4", "eleven_v3"])
async def test_expressive_provider_uses_dialogue(model):
    from pipecat.services.elevenlabs.dialogue.tts import ElevenLabsDialogueTTSService
    tts = elevenlabs.tts(voice="test-voice", model=model, api_key="test-key")._build()
    assert isinstance(tts, ElevenLabsDialogueTTSService)
    assert tts._settings.model == model
    assert tts._settings.voice == "test-voice"
    assert tts._settings.stability == .5
    assert "/text-to-dialogue/multi-stream-input" in tts._build_websocket_url()
    assert f"model_id={model}" in tts._build_websocket_url()


@pytest.mark.asyncio
async def test_flash_keeps_original_adapter():
    from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
    assert isinstance(elevenlabs.tts(api_key="test-key")._build(), ElevenLabsTTSService)


@pytest.mark.asyncio
async def test_dialogue_tags_flush_and_late_interrupted_audio(monkeypatch):
    """Exercise the actual pinned adapter, not a replacement fake TTS service."""
    tts = elevenlabs.tts(model="eleven_v4_turbo", api_key="test-key")._build()
    socket = AsyncMock()
    tts._websocket = socket
    tts._output_format = "pcm_24000"
    monkeypatch.setattr(tts, "_get_websocket", lambda: socket)
    await tts._send_context_init("old-turn")
    await tts._send_text("[whispers] Stay behind the painting. ", "old-turn")
    await tts.flush_audio("old-turn")
    sent = [json.loads(call.args[0]) for call in socket.send.call_args_list]
    assert sent[0]["voices"]
    assert sent[1]["inputs"][0]["text"].startswith("[whispers]")
    assert sent[2] == {"context_id": "old-turn", "flush": True}

    # Simulate audio-context invalidation performed by Pipecat on interruption.
    await tts._close_context("old-turn")
    await tts._send_context_init("new-turn")
    monkeypatch.setattr(tts, "audio_context_available", lambda cid: cid == "new-turn")
    appended = AsyncMock()
    monkeypatch.setattr(tts, "append_to_audio_context", appended)
    audio = base64.b64encode(b"\x01\x00" * 40).decode()
    await tts._handle_message({"context_id": "old-turn", "audio": audio})
    appended.assert_not_awaited()
    await tts._handle_message({"context_id": "new-turn", "audio": audio})
    assert appended.await_args.args[0] == "new-turn"
    assert appended.await_args.args[1].audio == b"\x01\x00" * 40
    # Old final cannot close or advance a new response.
    await tts._handle_message({"context_id": "old-turn", "is_final": True})
    assert "new-turn" in tts._contexts
    assert appended.await_count == 1


def test_prompt_is_bounded_and_preserves_waits():
    assert "[whispers]" in EXPRESSIVE_VOICE_INSTRUCTIONS
    assert "at most two" in EXPRESSIVE_VOICE_INSTRUCTIONS
    assert "timers are not supported" in EXPRESSIVE_VOICE_INSTRUCTIONS
    assert "clarification" in EXPRESSIVE_VOICE_INSTRUCTIONS


def test_demo_model_and_prompt_can_be_switched(monkeypatch):
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[3] / "apps/demo/server/app.py"
    monkeypatch.delenv("JEV_DEMO_FAKE", raising=False)
    monkeypatch.delenv("LIVEKIT_API_KEY", raising=False)
    monkeypatch.delenv("LIVEKIT_API_SECRET", raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **kw: None)
    for model, expressive in [("eleven_v4_turbo", True), ("eleven_flash_v2_5", False)]:
        monkeypatch.setenv("ELEVENLABS_TTS_MODEL", model)
        spec = importlib.util.spec_from_file_location("expressive_demo_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.EXPRESSIVE is expressive
        for agent in module.agents.values():
            assert agent.speak.model == model
            assert ("[whispers]" in agent.instructions) is expressive
