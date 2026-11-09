import asyncio
import pytest
from jev_pipecat import JevTurnController, TurnPolicy


class Client:
    decision = "yield"
    pause = {}
    calls = 0
    delay = 0
    error = False

    async def evaluate_turn(self, **kwargs):
        self.calls += 1
        await asyncio.sleep(self.delay)
        if self.error:
            raise TimeoutError()
        return {"decision": self.decision, "pause": self.pause}


def make(client):
    output, events = [], []
    async def handoff(text):
        output.append(text)
    controller = JevTurnController(client, on_yield=handoff, trace=events.append,
        policy=TurnPolicy(debounce_seconds=0, uncertain_seconds=.02,
            recheck_seconds=(.01, .02), max_silence_seconds=.12))
    return controller, output, events


@pytest.mark.asyncio
async def test_partial_handoff_late_final_exactly_once():
    c, output, _ = make(Client())
    c.transcript("Thursday works.")
    await asyncio.sleep(.01)
    assert output == ["Thursday works."]
    c.transcript("Thursday works!", final=True)
    await asyncio.sleep(.01)
    assert len(output) == 1
    c.transcript("Friday too", final=True)
    await asyncio.sleep(.01)
    assert len(output) == 2
    await c.close()


@pytest.mark.asyncio
async def test_new_complete_thought_replaces_hold():
    client = Client()
    client.decision = "hold"
    c, output, _ = make(client)
    c.transcript("The difference between", final=True)
    await asyncio.sleep(.01)
    assert not output
    client.decision = "yield"
    c.transcript("a debit card and a credit card?", final=True)
    await asyncio.sleep(.01)
    assert output == ["The difference between a debit card and a credit card?"]
    await c.close()


@pytest.mark.asyncio
async def test_speech_cancels_uncertain_and_stale_yield():
    client = Client()
    client.delay = .02
    c, output, _ = make(client)
    c.transcript("Hello")
    c.speech_started()
    await asyncio.sleep(.03)
    assert not output
    client.decision = "uncertain"
    c.speech_stopped()
    c.speech_started()
    await asyncio.sleep(.04)
    assert not output
    await c.close()


@pytest.mark.asyncio
async def test_hold_rechecks_then_bounded_release():
    client = Client()
    client.decision = "hold"
    c, output, events = make(client)
    c.transcript("I want to", final=True)
    await asyncio.sleep(.06)
    assert client.calls == 3 and not output
    await asyncio.sleep(.1)
    assert output == ["I want to"]
    handoffs = [e for e in events if e["kind"] == "handoff"]
    assert len(handoffs) == 1 and handoffs[0]["reason"] == "max_silence_timeout"
    assert next(e for e in events if e["kind"] == "silence_limit")["model_approved"] is False
    await c.close()


@pytest.mark.asyncio
async def test_duration_requests_have_no_special_timer():
    client = Client()
    client.decision = "hold"
    client.pause = {"waitRequested": True}
    c, output, events = make(client)
    c.transcript("Wait for 20 seconds", final=True)
    await asyncio.sleep(.17)
    assert output == ["Wait for 20 seconds"]
    assert not any(e["kind"] in {"wait_started", "wait_cancelled"} for e in events)
    assert all("pauseControl" not in e for e in events if e["kind"] == "jev_request")
    await c.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["error", "slow"])
async def test_provider_failure_or_hang_has_bounded_fallback(failure):
    client = Client()
    client.error = failure == "error"
    client.delay = 10 if failure == "slow" else 0
    c, output, _ = make(client)
    c.transcript("Hello", final=True)
    await asyncio.sleep(.17)
    assert output == ["Hello"]
    await asyncio.sleep(.03)
    assert len(output) == 1
    await c.close()


@pytest.mark.asyncio
async def test_transcript_revisions_and_repeated_stops_cannot_extend_silence():
    client = Client()
    client.decision = "hold"
    c, output, _ = make(client)
    c.transcript("I want")
    silence_at = c.silence_at
    for text in ["I want to", "I want to go", "I want to go to"]:
        await asyncio.sleep(.03)
        c.transcript(text)
        c.speech_stopped()
        assert c.silence_at == silence_at
    await asyncio.sleep(.06)
    assert output == ["I want to go to"]
    c.transcript("I want to go to.", final=True)
    await asyncio.sleep(.15)
    assert len(output) == 1
    await c.close()


@pytest.mark.asyncio
async def test_resumed_speech_cancels_limit_and_gets_fresh_pause_budget():
    client = Client()
    client.decision = "hold"
    c, output, _ = make(client)
    c.transcript("I want to", final=True)
    await asyncio.sleep(.03)
    c.speech_started()
    c.transcript("buy")
    await asyncio.sleep(.15)
    assert not output
    c.speech_stopped()
    await asyncio.sleep(.04)
    assert not output
    await asyncio.sleep(.12)
    assert output == ["I want to buy"]
    await c.close()


@pytest.mark.asyncio
async def test_close_cancels_both_tasks_and_idle_never_hands_off():
    client = Client()
    client.decision = "hold"
    c, output, _ = make(client)
    c.speech_stopped()
    await asyncio.sleep(.15)
    assert not output and c.silence_task is None
    c.transcript("I want to")
    tasks = [c.task, c.silence_task]
    await c.close()
    await asyncio.sleep(.15)
    assert not output and all(t.done() for t in tasks)


@pytest.mark.asyncio
async def test_late_model_result_cannot_duplicate_timeout_handoff():
    class StubbornClient:
        async def evaluate_turn(self, **kwargs):
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                # Even a provider that finishes after cancellation is stale.
                await asyncio.sleep(.01)
            return {"decision": "yield"}
    c, output, events = make(StubbornClient())
    c.transcript("Hello")
    await asyncio.sleep(.18)
    assert output == ["Hello"]
    assert len([e for e in events if e["kind"] == "handoff"]) == 1
    await c.close()


@pytest.mark.asyncio
async def test_uncertain_uses_shorter_silence_budget():
    client = Client()
    client.decision = "uncertain"
    c, output, events = make(client)
    c.transcript("Maybe")
    await asyncio.sleep(.06)
    assert output == ["Maybe"]
    assert [e["reason"] for e in events if e["kind"] == "handoff"] == ["uncertain_timeout"]
    await asyncio.sleep(.1)
    assert len(output) == 1
    await c.close()


@pytest.mark.parametrize("kwargs", [{"max_silence_seconds": 0}, {"max_silence_seconds": float("inf")}, {"uncertain_seconds": float("nan")}])
def test_policy_rejects_unbounded_or_invalid_timeouts(kwargs):
    with pytest.raises(ValueError):
        TurnPolicy(**kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["yield", "hold", "uncertain", "error"])
async def test_gate_real_pipeline_partial_and_late_final(decision):
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.task import PipelineTask
    from pipecat.pipeline.runner import PipelineRunner
    from pipecat.frames.frames import InterimTranscriptionFrame, TranscriptionFrame, LLMContextFrame
    from pipecat.processors.frame_processor import FrameProcessor
    from pipecat.processors.aggregators.llm_context import LLMContext
    from jev_pipecat import JevVoiceAgent

    received = asyncio.Event()
    contexts = []
    class Sink(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if isinstance(frame, LLMContextFrame):
                contexts.append(list(frame.context.get_messages()))
                received.set()
            await self.push_frame(frame, direction)
    client = Client()
    client.decision = decision
    client.error = decision == "error"
    gate = JevVoiceAgent(client, context=LLMContext([]), policy=TurnPolicy(
        debounce_seconds=0, uncertain_seconds=.02, max_silence_seconds=.06))
    pair = gate.aggregators(vad_analyzer=None)
    task = PipelineTask(Pipeline([gate, pair.user(), Sink()]))
    run = asyncio.create_task(PipelineRunner(handle_sigint=False).run(task))
    try:
        await task.queue_frame(InterimTranscriptionFrame("Thursday works", "u", ""))
        await asyncio.wait_for(received.wait(), 10)
        await task.queue_frame(TranscriptionFrame("Thursday works.", "u", ""))
        await asyncio.sleep(.05)
        assert contexts == [[{"role": "user", "content": "Thursday works"}]]
    finally:
        await task.cancel()
        await run
