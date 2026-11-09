import importlib.util
from pathlib import Path

import pytest

from jev.providers import openai


@pytest.mark.asyncio
@pytest.mark.parametrize("model,effort", [("gpt-6-luna", "none"), ("gpt-4.1-mini", None)])
async def test_streamed_request_settings(model, effort):
    from pipecat.processors.aggregators.llm_context import LLMContext
    llm = openai.llm(model, api_key="test-key", reasoning_effort=effort)._build()
    context = LLMContext([{"role": "user", "content": "Hello"}])
    params = llm.build_chat_completion_params(llm.get_llm_adapter().get_llm_invocation_params(
        context, convert_developer_to_user=False))
    assert params["model"] == model
    assert params["stream"] is True
    if effort is None:
        assert "reasoning_effort" not in params
    else:
        assert params["reasoning_effort"] == effort
    await llm._client.close()


def test_sdk_default_is_unchanged():
    assert openai.llm().model == "gpt-4.1-mini"
    assert openai.llm().reasoning_effort is None


@pytest.mark.asyncio
@pytest.mark.parametrize("override,expected,effort", [(None, "gpt-6-luna", "none"), ("gpt-4.1-mini", "gpt-4.1-mini", None)])
async def test_demo_default_and_override(monkeypatch, override, expected, effort):
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **kw: None)
    for key in ("JEV_DEMO_FAKE", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "VOICE_LLM_MODEL"):
        monkeypatch.delenv(key, raising=False)
    if override:
        monkeypatch.setenv("VOICE_LLM_MODEL", override)
    path = Path(__file__).resolve().parents[3] / "apps/demo/server/app.py"
    spec = importlib.util.spec_from_file_location("luna_demo_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for agent in module.agents.values():
        assert agent.think.model == expected
        assert agent.think.reasoning_effort == effort
    config = await module.config()
    assert config["llm_model"] == expected
    assert config["llm_reasoning_effort"] == effort
