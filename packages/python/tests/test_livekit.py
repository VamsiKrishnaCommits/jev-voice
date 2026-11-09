import asyncio
import pytest
from jev.livekit import LiveKitController, LiveKitTurnTaking


class Backend:
    complete = False
    delay = 0
    calls = 0
    flushed = False
    error = False

    async def predict(self):
        self.calls += 1
        await asyncio.sleep(self.delay)
        if self.error:
            raise TimeoutError()
        return {"complete": self.complete, "model": "turn-detector-v1"}

    def cancel_prediction(self):
        pass

    def flush(self):
        self.flushed = True


def make(backend):
    output, events = [], []
    async def handoff(text):
        output.append(text)
    c = LiveKitController(backend, config=LiveKitTurnTaking(min_delay=.01, max_delay=.07),
        on_yield=handoff, trace=events.append)
    c.vad_stop_secs = 0
    c.speech_started()
    c.transcript("A question", final=True)
    return c, output, events


@pytest.mark.asyncio
async def test_low_probability_waits_for_bounded_timeout():
    backend = Backend()
    c, output, events = make(backend)
    c.speech_stopped()
    await asyncio.sleep(.03)
    assert not output
    await asyncio.sleep(.06)
    assert output == ["A question"] and backend.flushed
    assert [e["reason"] for e in events if e["kind"] == "handoff"] == ["livekit_max_delay"]
    await c.close()


@pytest.mark.asyncio
async def test_high_probability_releases_and_ignores_consumed_final():
    backend = Backend()
    backend.complete = True
    c, output, events = make(backend)
    c.speech_stopped()
    await asyncio.sleep(.03)
    assert len(output) == 1
    assert [e["reason"] for e in events if e["kind"] == "handoff"] == ["livekit_yield"]
    await c.close()


@pytest.mark.asyncio
async def test_new_speech_cancels_release_and_no_request_while_speaking():
    backend = Backend()
    c, output, _ = make(backend)
    await asyncio.sleep(.01)
    assert backend.calls == 0
    c.speech_stopped()
    await asyncio.sleep(.02)
    c.speech_started()
    await asyncio.sleep(.09)
    assert not output
    await c.close()


@pytest.mark.asyncio
async def test_transcript_update_reuses_prediction_without_resetting_deadline():
    backend = Backend()
    c, output, events = make(backend)
    c.speech_stopped()
    await asyncio.sleep(.03)
    c.transcript("with a continuation", final=True)
    await asyncio.sleep(.055)
    assert output == ["A question with a continuation"]
    assert backend.calls == 1
    assert len([e for e in events if e["kind"] == "livekit_response"]) == 1
    await c.close()


@pytest.mark.asyncio
async def test_error_is_explicit_timeout_not_model_prediction():
    backend = Backend()
    backend.error = True
    c, output, events = make(backend)
    c.speech_stopped()
    await asyncio.sleep(.09)
    assert output
    assert any(e["kind"] == "livekit_error" and e["model_prediction"] is False for e in events)
    assert not any(e["kind"] == "livekit_response" for e in events)
    await c.close()


@pytest.mark.asyncio
async def test_close_cancels_pending_prediction():
    backend = Backend()
    backend.delay = 100
    c, output, _ = make(backend)
    c.speech_stopped()
    await asyncio.sleep(.01)
    task = c.prediction
    await c.close()
    assert task.cancelled() and not output


def test_config_validation():
    for args in ({"min_delay": -1}, {"max_delay": .1}, {"prediction_timeout": 0}, {"max_delay": float("inf")}):
        with pytest.raises(ValueError):
            LiveKitTurnTaking(**args)
