"""Demo readiness must be useful without leaking credentials or making calls."""
import importlib.util
from pathlib import Path

import pytest


def load_demo(monkeypatch, *, fake=False):
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **kw: None)
    for key in ("JEV_DEMO_FAKE", "ELEVENLABS_API_KEY", "ELEVEN_LABS",
                "OPENAI_API_KEY", "TYPESAFE_API_KEY", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
        monkeypatch.delenv(key, raising=False)
    if fake:
        monkeypatch.setenv("JEV_DEMO_FAKE", "1")
    path = Path(__file__).resolve().parents[3] / "apps/demo/server/app.py"
    monkeypatch.syspath_prepend(str(path.parent))
    spec = importlib.util.spec_from_file_location("setup_demo_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_missing_keys_and_ready_modes(monkeypatch):
    module = load_demo(monkeypatch)
    config = await module.config()
    assert config["setup"]["jev"]["missing"] == ["ELEVENLABS_API_KEY", "OPENAI_API_KEY", "TYPESAFE_API_KEY"]
    assert config["setup"]["silence"]["missing"] == ["ELEVENLABS_API_KEY", "OPENAI_API_KEY"]
    assert "livekit" not in config["modes"]
    for key in ("ELEVENLABS_API_KEY", "OPENAI_API_KEY", "TYPESAFE_API_KEY"):
        monkeypatch.setenv(key, "secret-not-for-config")
    config = await module.config()
    assert all(value["ready"] for value in config["setup"].values())
    assert "secret-not-for-config" not in str(config)


@pytest.mark.asyncio
async def test_offline_needs_no_keys(monkeypatch):
    config = await load_demo(monkeypatch, fake=True).config()
    assert config["fake"] is True
    assert set(config["modes"]) == {"jev", "silence"}
    assert all(value == {"ready": True, "missing": []} for value in config["setup"].values())


def test_optional_livekit_dependency(monkeypatch):
    module = load_demo(monkeypatch)
    def unavailable(*args):
        raise ModuleNotFoundError("Optional dependency absent")
    monkeypatch.setattr(module.importlib.util, "find_spec", unavailable)
    assert module.livekit_installed() is False
