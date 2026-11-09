import asyncio
import pytest
from pipecat.frames.frames import TranscriptionFrame, InterimTranscriptionFrame, VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame
from jev_pipecat import JevTurnStopStrategy, jev_user_params


class Client:
    def __init__(self, decision="yield", delay=0, error=False):
        self.decision, self.delay, self.error = decision, delay, error

    async def evaluate(self, text, context):
        await asyncio.sleep(self.delay)
        if self.error:
            raise TimeoutError()
        return {"decision": self.decision}


def strategy(client, **kwargs):
    obj = JevTurnStopStrategy(client, **kwargs)
    events = []

    @obj.event_handler("on_user_turn_inference_triggered")
    async def inference(*args):
        events.append("inference")

    @obj.event_handler("on_user_turn_stopped")
    async def stopped(*args):
        events.append("stopped")
        await obj.handle_user_turn_stopped()

    return obj, events


@pytest.mark.asyncio
async def test_yield_once():
    obj, events = strategy(Client())
    await obj.process_frame(TranscriptionFrame("Hello", "u", ""))
    await asyncio.sleep(.02)
    assert events == ["inference", "stopped"]
    await obj.process_frame(VADUserStoppedSpeakingFrame())
    await asyncio.sleep(.02)
    assert len(events) == 2
    await obj.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["hold", "yield"])
async def test_speaking_blocks_handoff(decision):
    obj, events = strategy(Client(decision))
    await obj.process_frame(VADUserStartedSpeakingFrame())
    await obj.process_frame(TranscriptionFrame("and", "u", ""))
    await asyncio.sleep(.02)
    assert not events
    await obj.cleanup()


@pytest.mark.asyncio
async def test_hold_and_provider_error_fail_closed():
    client = Client("hold")
    obj, events = strategy(client, uncertain_seconds=.001)
    await obj.process_frame(TranscriptionFrame("I want to", "u", ""))
    await asyncio.sleep(.03)
    assert not events
    client.error = True
    await obj.process_frame(TranscriptionFrame("actually", "u", ""))
    await asyncio.sleep(.03)
    assert not events
    assert jev_user_params(client).user_turn_stop_timeout == float("inf")
    await obj.cleanup()


@pytest.mark.asyncio
async def test_new_partial_cancels_stale_yield():
    obj, events = strategy(Client(delay=.02))
    await obj.process_frame(TranscriptionFrame("Hello", "u", ""))
    await obj.process_frame(InterimTranscriptionFrame("but", "u", ""))
    await asyncio.sleep(.04)
    assert not events
    await obj.cleanup()


@pytest.mark.asyncio
async def test_uncertain_timeout_cancellable():
    obj, events = strategy(Client("uncertain"), uncertain_seconds=.04)
    await obj.process_frame(TranscriptionFrame("maybe", "u", ""))
    await asyncio.sleep(.01)
    assert not events
    await obj.process_frame(VADUserStartedSpeakingFrame())
    await asyncio.sleep(.05)
    assert not events
    await obj.process_frame(VADUserStoppedSpeakingFrame())
    await asyncio.sleep(.06)
    assert events == ["inference", "stopped"]
    await obj.cleanup()


@pytest.mark.asyncio
async def test_real_pipecat_aggregator_handoff():
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.task import PipelineTask
    from pipecat.pipeline.runner import PipelineRunner
    from pipecat.frames.frames import LLMContextFrame
    from pipecat.processors.frame_processor import FrameProcessor
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair

    contexts = []
    received = asyncio.Event()

    class Sink(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if isinstance(frame, LLMContextFrame):
                contexts.append(list(frame.context.get_messages()))
                received.set()
            await self.push_frame(frame, direction)

    context = LLMContext([])
    pair = LLMContextAggregatorPair(context, user_params=jev_user_params(Client()))
    task = PipelineTask(Pipeline([pair.user(), Sink()]))
    run = asyncio.create_task(PipelineRunner(handle_sigint=False).run(task))
    try:
        await task.queue_frame(TranscriptionFrame("Thursday works.", "u", ""))
        await asyncio.wait_for(received.wait(), 10)
        assert contexts == [[{"role": "user", "content": "Thursday works."}]]
    finally:
        await task.cancel()
        await run
