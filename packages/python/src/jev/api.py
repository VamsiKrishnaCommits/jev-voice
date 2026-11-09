import asyncio
import base64
from dataclasses import dataclass
from uuid import uuid4
from .turns import JevTurnTaking, StrategyAdapter


@dataclass(frozen=True)
class Audio:
    data: bytes
    sample_rate: int
    response_id: str
    channels: int = 1
    encoding: str = "pcm_s16le"

    def base64(self):
        return base64.b64encode(self.data).decode()


@dataclass(frozen=True)
class ClearAudio:
    response_id: str


class Agent:
    def __init__(self, *, instructions, listen, think, speak, turn_taking=None,
                 trace=None, uncertain_timeout=1.5, max_silence_timeout=4):
        from jev_pipecat.controller import TurnPolicy
        TurnPolicy(uncertain_seconds=uncertain_timeout, max_silence_seconds=max_silence_timeout)
        self.instructions = instructions
        self.listen, self.think, self.speak = listen, think, speak
        self.turn_taking = turn_taking if turn_taking is not None else JevTurnTaking()
        # trace is a factory: no mutable trace session is shared between callers.
        self.trace_factory = trace
        self.uncertain_timeout = uncertain_timeout
        self.max_silence_timeout = max_silence_timeout

    def conversation(self, *, sample_rate=16000, input_format="pcm_s16le"):
        if input_format != "pcm_s16le" or sample_rate not in (8000, 16000, 24000, 48000):
            raise ValueError("Expected mono PCM16 at 8000, 16000, 24000 or 48000 Hz")
        return Conversation(self, sample_rate)

    def _components(self, on_trace=None):
        from pipecat.processors.aggregators.llm_context import LLMContext
        from pipecat.audio.vad.silero import SileroVADAnalyzer
        from jev_pipecat import JevVoiceAgent, TurnPolicy
        context = LLMContext([{"role": "system", "content": self.instructions}])
        recorder = self.trace_factory() if self.trace_factory else lambda event: None
        def trace(event):
            recorder({"strategy": type(self.turn_taking).__name__, **event})
            if on_trace:
                on_trace({"strategy": type(self.turn_taking).__name__, **event})
        if hasattr(self.turn_taking, "_build_gate"):
            gate, vad = self.turn_taking._build_gate(context=context, trace=trace)
        else:
            gate = JevVoiceAgent(StrategyAdapter(self.turn_taking), context=context,
                policy=TurnPolicy(uncertain_seconds=self.uncertain_timeout,
                    max_silence_seconds=self.max_silence_timeout), trace=trace)
            vad = SileroVADAnalyzer()
        pair = gate.aggregators(vad_analyzer=vad)
        return self.listen._build(), gate, pair, self.think._build(), self.speak._build()

    async def _run_browser(self, runner_args):
        from pipecat.runner.utils import create_transport
        from pipecat.transports.base_transport import TransportParams
        from pipecat.pipeline.pipeline import Pipeline
        from pipecat.pipeline.task import PipelineTask, PipelineParams
        from pipecat.pipeline.runner import PipelineRunner
        from jev_pipecat.observer import JevRTVIProcessor
        transport = await create_transport(runner_args, {"webrtc": lambda: TransportParams(
            audio_in_enabled=True, audio_out_enabled=True)})
        stt, gate, pair, llm, tts = self._components()
        task = PipelineTask(Pipeline([transport.input(), stt, gate, pair.user(), llm,
            tts, transport.output(), pair.assistant()]), rtvi_processor=JevRTVIProcessor(),
            params=PipelineParams(enable_metrics=True, enable_usage_metrics=True))
        @transport.event_handler("on_client_disconnected")
        async def disconnected(transport, client):
            await task.cancel()
        await PipelineRunner(handle_sigint=False).run(task)

    def serve(self, *, host="127.0.0.1", port=7861, configure_app=None):
        """Local browser demo. Blocking; call once from the main thread.

        Not a production authentication or deployment layer.
        """
        import sys
        from pipecat.runner.run import main, app
        if configure_app:
            configure_app(app)
        module = sys.modules["__main__"]
        previous = getattr(module, "bot", None)
        argv = sys.argv
        module.bot = self._run_browser
        sys.argv = [argv[0], "--host", host, "--port", str(port), "-t", "webrtc"]
        try:
            main()
        finally:
            sys.argv = argv
            if previous is None:
                del module.bot
            else:
                module.bot = previous


class Conversation:
    """One full-duplex conversation. Input exhaustion closes the conversation.

    Keep input open while listening for the answer; leaving the context cancels
    model calls. PCM is mono signed little-endian 16-bit, without WAV headers.
    """
    def __init__(self, agent, sample_rate):
        self.agent, self.sample_rate = agent, sample_rate
        self.events = asyncio.Queue(maxsize=256)
        self.task = self.runner = self.feeder = None
        self.started = False
        self.closed = False
        self.gate = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def stream(self, incoming_audio):
        if self.started or self.closed:
            raise RuntimeError("A conversation can be streamed only once")
        self.started = True
        from pipecat.pipeline.pipeline import Pipeline
        from pipecat.pipeline.task import PipelineTask, PipelineParams
        from pipecat.pipeline.runner import PipelineRunner
        from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
        from pipecat.frames.frames import (InputAudioRawFrame, TTSAudioRawFrame,
            InterruptionFrame, LLMFullResponseStartFrame, ErrorFrame)
        stt, self.gate, pair, llm, tts = self.agent._components()
        conversation = self

        class Playback(FrameProcessor):
            def __init__(self):
                super().__init__()
                self.response_id = str(uuid4())

            async def process_frame(self, frame, direction):
                await super().process_frame(frame, direction)
                if direction == FrameDirection.DOWNSTREAM:
                    if isinstance(frame, LLMFullResponseStartFrame):
                        self.response_id = str(uuid4())
                    elif isinstance(frame, InterruptionFrame):
                        # Drop application-buffered output before yielding clear.
                        while not conversation.events.empty():
                            conversation.events.get_nowait()
                        conversation.events.put_nowait(ClearAudio(self.response_id))
                    elif isinstance(frame, TTSAudioRawFrame):
                        if conversation.events.full():
                            while not conversation.events.empty():
                                conversation.events.get_nowait()
                            conversation.events.put_nowait(RuntimeError("Playback consumer too slow; conversation buffer full"))
                        else:
                            conversation.events.put_nowait(Audio(frame.audio, frame.sample_rate, self.response_id, frame.num_channels))
                if isinstance(frame, ErrorFrame):
                    # Errors never masquerade as normal playback events.
                    if not conversation.events.full():
                        conversation.events.put_nowait(RuntimeError(frame.error))
                await self.push_frame(frame, direction)

        self.task = PipelineTask(Pipeline([stt, self.gate, pair.user(), llm, tts,
            Playback(), pair.assistant()]), enable_rtvi=False,
            params=PipelineParams(audio_in_sample_rate=self.sample_rate, audio_out_sample_rate=24000))
        self.runner = asyncio.create_task(PipelineRunner(handle_sigint=False).run(self.task))

        async def feed():
            async for chunk in incoming_audio:
                if not isinstance(chunk, bytes) or len(chunk) % 2:
                    raise ValueError("Audio chunks must be aligned PCM16 bytes")
                if len(chunk) > self.sample_rate * 2:
                    raise ValueError("Send audio chunks of at most one second")
                await self.task.queue_frame(InputAudioRawFrame(chunk, self.sample_rate, 1))

        self.feeder = asyncio.create_task(feed())
        try:
            while True:
                event = asyncio.create_task(self.events.get())
                try:
                    done, _ = await asyncio.wait([event, self.feeder, self.runner], return_when=asyncio.FIRST_COMPLETED)
                    if self.feeder in done:
                        await self.feeder
                        break
                    if self.runner in done:
                        await self.runner
                        break
                    item = event.result()
                    if isinstance(item, Exception):
                        raise item
                    yield item
                finally:
                    if not event.done():
                        event.cancel()
                    await asyncio.gather(event, return_exceptions=True)
        finally:
            await self.close()

    async def respond(self):
        if self.gate:
            await self.gate.controller.release()

    async def close(self):
        if self.closed:
            return
        self.closed = True
        if self.feeder:
            self.feeder.cancel()
            await asyncio.gather(self.feeder, return_exceptions=True)
        if self.task:
            await self.task.cancel()
        if self.runner:
            await self.runner
