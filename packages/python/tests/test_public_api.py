import asyncio
import pytest
from jev import Agent, Audio, Decision, JevTurnTaking, SilenceTurnTaking, Turn
from jev.turns import StrategyAdapter


@pytest.mark.asyncio
async def test_silence_does_not_call_jev_and_waits():
    strategy = SilenceTurnTaking(.02)
    assert (await strategy.decide(Turn("hello", [], True, 0))).action == "hold"
    task = asyncio.create_task(strategy.decide(Turn("hello", [], False, 0)))
    await asyncio.sleep(.005)
    assert not task.done()
    assert (await task).action == "yield"


@pytest.mark.asyncio
async def test_custom_strategy_and_session_factory():
    created = []
    class Custom:
        def create(self):
            child = Custom()
            created.append(child)
            return child
        async def decide(self, turn):
            return Decision("yield" if turn.text.endswith("done") else "hold")
    a, b = StrategyAdapter(Custom()), StrategyAdapter(Custom())
    assert a.strategy is not b.strategy
    result = await a.evaluate_turn(text="done", context=[], timing={"speaking": False, "silenceMs": 0})
    assert result["decision"] == "yield"


@pytest.mark.asyncio
async def test_new_continuation_not_lost():
    from jev_pipecat.controller import JevTurnController, TurnPolicy
    output = []
    async def handoff(text): output.append(text)
    c = JevTurnController(StrategyAdapter(SilenceTurnTaking(0)), on_yield=handoff,
        policy=TurnPolicy(debounce_seconds=0))
    c.transcript("Uh...")
    await asyncio.sleep(.01)
    c.speech_started()
    c.transcript("Uh, I think I'm fine.", final=True)
    c.speech_stopped()
    await asyncio.sleep(.01)
    assert output == ["Uh...", "I think I'm fine."]
    await c.close()


@pytest.mark.asyncio
async def test_public_stream_emits_audio_and_cleans_up():
    from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
    from pipecat.frames.frames import InputAudioRawFrame, InterimTranscriptionFrame, LLMContextFrame, LLMFullResponseStartFrame, TTSAudioRawFrame
    class STT(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if isinstance(frame, InputAudioRawFrame):
                await self.push_frame(InterimTranscriptionFrame("Hello", "u", ""))
            await self.push_frame(frame, direction)
    class LLM(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if isinstance(frame, LLMContextFrame):
                await self.push_frame(LLMFullResponseStartFrame())
                await self.push_frame(TTSAudioRawFrame(b"\0\0" * 100, 24000, 1))
            else:
                await self.push_frame(frame, direction)
    class Passthrough(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            await self.push_frame(frame, direction)
    class Factory:
        def __init__(self, cls): self.cls = cls
        def _build(self): return self.cls()
    agent = Agent(instructions="test", listen=Factory(STT), think=Factory(LLM),
                  speak=Factory(Passthrough), turn_taking=SilenceTurnTaking(0))
    async def mic():
        yield b"\0\0" * 160
        await asyncio.Event().wait()
    async with agent.conversation() as conversation:
        stream = conversation.stream(mic())
        async with asyncio.timeout(10):
            async for event in stream:
                if isinstance(event, Audio):
                    assert event.sample_rate == 24000 and event.response_id
                    break
        await stream.aclose()
    assert conversation.closed and conversation.runner.done() and conversation.feeder.done()


def test_validation():
    with pytest.raises(ValueError): SilenceTurnTaking(-1)
    with pytest.raises(ValueError): Decision("banana")


@pytest.mark.asyncio
async def test_instructions_and_examples_extend_prompt(monkeypatch):
    from jev_pipecat.turns import JevClient
    import aiohttp
    captured = []
    class Response:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def json(self):
            return {"answers": {"turn": {"type": "choice", "choice": "yield",
                "probabilities": {"yield": 1, "hold": 0, "uncertain": 0}}}}
    class Session:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def post(self, url, **kwargs):
            captured.append(kwargs["json"])
            return Response()
    monkeypatch.setattr(aiohttp, "ClientSession", Session)
    await JevClient("test", instructions="Allow dictation", examples=[{"user": "A seven", "decision": "hold"}]).evaluate("hi", [])
    prompt = captured[0]["questions"]["turn"]["instructions"]
    assert "Judge the latest ending" in prompt and "Allow dictation" in prompt and "A seven" in prompt
    assert set(captured[0]["questions"]) == {"turn"}
    assert "pause_control" not in captured[0]["state"]
    decision = await JevTurnTaking(api_key="test").decide(Turn("That's all.", [], False, .5))
    assert decision.action == "yield"
    assert set(captured[-1]["questions"]) == {"turn"}
