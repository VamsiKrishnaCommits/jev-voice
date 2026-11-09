"""Thin Pipecat binding around the independent Jev turn controller."""
import json
import time
from pathlib import Path
from uuid import uuid4

from pipecat.frames.frames import (TranscriptionFrame, InterimTranscriptionFrame,
    VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame, CancelFrame, EndFrame)
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
from pipecat.turns.user_stop.base_user_turn_stop_strategy import BaseUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.processors.aggregators.llm_response_universal import LLMUserAggregatorParams, LLMContextAggregatorPair
from .controller import JevTurnController


class ApprovedTranscript(TranscriptionFrame):
    """Only transcripts approved by our controller may start inference."""


class ApprovedStopStrategy(BaseUserTurnStopStrategy):
    async def process_frame(self, frame):
        if isinstance(frame, ApprovedTranscript):
            # Aggregator has already appended this exact text to context.
            await self.trigger_user_turn_stopped()


class JsonlTrace:
    def __init__(self, directory):
        self.session_id = str(uuid4())
        self.started = time.monotonic()
        self.path = Path(directory) / f"{self.session_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, event):
        entry = {"sessionId": self.session_id, "at": time.time(),
                 "elapsedMs": round((time.monotonic() - self.started) * 1000), **event}
        with self.path.open("a") as stream:
            stream.write(json.dumps(entry, default=str) + "\n")


class JevVoiceAgent(FrameProcessor):
    """Place after STT, before this agent's user aggregator.

    Supply Silero to user_params(); its VAD signals travel upstream to this gate
    and the STT. All unapproved transcripts are consumed here, not buffered by
    Pipecat. Thus cancel/disconnect cannot flush an unapproved turn into the LLM.
    """
    def __init__(self, client, *, context, policy=None, trace=lambda event: None):
        super().__init__()
        self.context = context
        self.controller = JevTurnController(client, on_yield=self._handoff,
            context=context.get_messages, policy=policy, trace=trace)

    def aggregators(self, *, vad_analyzer):
        return LLMContextAggregatorPair(self.context, user_params=LLMUserAggregatorParams(
            vad_analyzer=vad_analyzer, user_turn_stop_timeout=float("inf"),
            user_turn_strategies=UserTurnStrategies(stop=[ApprovedStopStrategy()])))

    async def _handoff(self, text):
        await self.push_frame(ApprovedTranscript(text, "user", "", finalized=True))

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, (CancelFrame, EndFrame)):
            await self.controller.close()
        elif isinstance(frame, VADUserStartedSpeakingFrame):
            self.controller.speech_started()
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            self.controller.speech_stopped()
        elif direction == FrameDirection.DOWNSTREAM and isinstance(frame, (InterimTranscriptionFrame, TranscriptionFrame)):
            self.controller.transcript(frame.text, final=isinstance(frame, TranscriptionFrame))
            return
        await self.push_frame(frame, direction)

    async def cleanup(self):
        await self.controller.close()
        await super().cleanup()
