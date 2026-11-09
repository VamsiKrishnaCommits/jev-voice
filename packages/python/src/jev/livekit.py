"""Experimental hosted LiveKit audio detector adapter, not a full LiveKit AgentSession.

The SDK's calibrated v1 predictions control a bounded endpointing policy. Existing
Pipecat providers, transcript ownership, interruption and WebRTC remain unchanged.
No local-mini or synthetic prediction is ever reported as a successful v1 result.
"""
import asyncio
import math
import time
from dataclasses import dataclass

from jev_pipecat.controller import JevTurnController


@dataclass(frozen=True)
class LiveKitTurnTaking:
    min_delay: float = .3
    max_delay: float = 2.5
    prediction_timeout: float = 1.2

    def __post_init__(self):
        if (not all(math.isfinite(x) for x in (self.min_delay, self.max_delay, self.prediction_timeout))
                or not 0 <= self.min_delay <= self.max_delay or self.prediction_timeout <= 0):
            raise ValueError("Expected finite 0 <= min_delay <= max_delay and a positive prediction timeout")

    def _build_gate(self, *, context, trace):
        from pipecat.audio.vad.silero import SileroVADAnalyzer
        from pipecat.audio.vad.vad_analyzer import VADParams
        gate = LiveKitGate(context=context, config=self, trace=trace)
        return gate, SileroVADAnalyzer(params=VADParams(stop_secs=.55))


class LiveKitController(JevTurnController):
    """Reuse transcript accumulation/stale-result safety, not Jev's prompt or hold timer."""
    def __init__(self, backend, *, config, **kwargs):
        super().__init__(None, **kwargs)
        self.backend, self.config = backend, config
        self.prediction = None
        self.had_speech = False
        self.vad_stop_secs = .55

    def speech_started(self):
        self.had_speech = True
        self.backend.cancel_prediction()
        self.prediction = None
        super().speech_started()

    def _ensure_silence_limit(self):
        # This adapter owns its separate bounded endpointing policy.
        pass

    def speech_stopped(self):
        super().speech_stopped()
        # Count the silence already consumed by VAD, not a second full endpoint delay.
        self.silence_at -= self.vad_stop_secs

    async def _evaluate(self, revision):
        if self.speaking or not self.had_speech:
            return
        try:
            if self.prediction is None:
                self.emit("livekit_request", model="turn-detector-v1", input="streaming_audio")
                self.prediction = asyncio.create_task(self.backend.predict())
            result = await asyncio.shield(self.prediction)
            if revision != self.revision or self.closed or self.speaking:
                return
            # emit once per pause, even when a late STT update reuses this prediction.
            if not result.get("reported"):
                self.emit("livekit_response", **result)
                result["reported"] = True
                if not result["complete"]:
                    self.emit("hold", source="livekit", bounded=True, max_delay=self.config.max_delay)
            delay = self.config.min_delay if result["complete"] else self.config.max_delay
            reason = "livekit_yield" if result["complete"] else "livekit_max_delay"
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if revision != self.revision or self.closed:
                return
            self.emit("livekit_error", error=type(error).__name__,
                message="Hosted v1 did not produce a verified prediction; using the bounded timeout, not mini.",
                fallback="silence_timeout", model_prediction=False)
            delay, reason = self.config.max_delay, "livekit_unavailable_timeout"
        await asyncio.sleep(max(0, self.silence_at + delay - time.monotonic()))
        if revision == self.revision and not self.speaking and not self.closed:
            await self.release(reason)

    async def release(self, reason="manual"):
        if self.speaking or not self.text or self.closed:
            return
        self.backend.flush()
        self.prediction = None
        self.had_speech = False
        await super().release(reason)

    async def close(self):
        prediction = self.prediction
        await super().close()
        self.backend.cancel_prediction()
        if prediction:
            prediction.cancel()
            await asyncio.gather(prediction, return_exceptions=True)


from jev_pipecat.agent import JevVoiceAgent
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
from pipecat.frames.frames import StartFrame, InputAudioRawFrame, VADUserStoppedSpeakingFrame


class LiveKitGate(JevVoiceAgent):
    def __init__(self, *, context, config, trace):
        FrameProcessor.__init__(self)
        self.context, self.config = context, config
        self.stream = self.http = None
        self.controller = LiveKitController(self, config=config, on_yield=self._handoff,
            context=context.get_messages, trace=trace)

    async def _start_detector(self):
        import aiohttp
        from livekit.agents import inference, APIConnectOptions
        self.http = aiohttp.ClientSession()
        try:
            detector = inference.TurnDetector(version="v1", local_fallback=False, http_session=self.http)
            self.stream = detector.stream(conn_options=APIConnectOptions(max_retry=0, timeout=10))
        except Exception:
            await self.http.close()
            raise

    async def process_frame(self, frame, direction):
        if isinstance(frame, StartFrame):
            await self._start_detector()
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            self.controller.vad_stop_secs = frame.stop_secs
        elif direction == FrameDirection.DOWNSTREAM and isinstance(frame, InputAudioRawFrame) and self.stream:
            from livekit import rtc
            self.stream.push_audio(rtc.AudioFrame(frame.audio, frame.sample_rate, frame.num_channels,
                len(frame.audio) // (2 * frame.num_channels)))
        await super().process_frame(frame, direction)

    async def predict(self):
        from livekit.agents.language import LanguageCode
        stream = self.stream
        if not stream:
            raise RuntimeError("Detector not started")
        started = time.monotonic()
        try:
            async with asyncio.timeout(self.config.prediction_timeout):
                while await stream.unlikely_threshold(LanguageCode("en")) is None:
                    if stream.is_degraded:
                        raise RuntimeError("Hosted detector unavailable")
                    await asyncio.sleep(.02)
                event = await stream.predict()
            if stream.is_degraded or stream.is_fallback or event.inference_duration is None:
                raise RuntimeError("Not a verified hosted prediction")
            threshold = await stream.unlikely_threshold(LanguageCode("en"))
            probability = event.end_of_turn_probability
            if threshold is None or not math.isfinite(probability):
                raise RuntimeError("Invalid detector result")
            return {"model": stream.model, "probability": probability, "threshold": threshold,
                "complete": probability >= threshold, "decision": "yield" if probability >= threshold else "hold",
                "latency_ms": round((time.monotonic()-started)*1000),
                "server_inference_ms": round(event.inference_duration*1000), "fallback": False}
        finally:
            stream.cancel_inference()

    def cancel_prediction(self):
        if self.stream:
            self.stream.cancel_inference()
        if self.controller.prediction and not self.controller.prediction.done():
            self.controller.prediction.cancel()

    def flush(self):
        if self.stream:
            self.stream.flush()

    async def cleanup(self):
        await super().cleanup()
        if self.stream:
            await self.stream.aclose()
            self.stream = None
        if self.http:
            await self.http.close()
            self.http = None
