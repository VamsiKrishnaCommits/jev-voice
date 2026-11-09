"""Deterministic offline providers for transport tests, never used by default."""
import math
import struct
from jev import Agent, SilenceTurnTaking
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.frames.frames import (InputAudioRawFrame, TranscriptionFrame, LLMContextFrame,
    LLMFullResponseStartFrame, LLMFullResponseEndFrame, LLMTextFrame, TTSAudioRawFrame,
    TTSStartedFrame, TTSStoppedFrame)


class FakeSTT(FrameProcessor):
    def __init__(self):
        super().__init__()
        self.chunks = 0

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame):
            self.chunks += 1
            if self.chunks == 50:
                await self.push_frame(TranscriptionFrame("Offline transport check.", "user", ""))
            return
        await self.push_frame(frame, direction)


class FakeLLM(FrameProcessor):
    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMContextFrame):
            await self.push_frame(LLMFullResponseStartFrame())
            await self.push_frame(LLMTextFrame("Synthetic response: transport is working."))
            await self.push_frame(LLMFullResponseEndFrame())
        else:
            await self.push_frame(frame, direction)


class FakeTTS(FrameProcessor):
    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMTextFrame):
            await self.push_frame(TTSStartedFrame())
            audio = b"".join(struct.pack("<h", int(1000 * math.sin(i * 2 * math.pi * 440 / 24000))) for i in range(4800))
            await self.push_frame(TTSAudioRawFrame(audio, 24000, 1))
            await self.push_frame(TTSStoppedFrame())
        await self.push_frame(frame, direction)


class Factory:
    def __init__(self, cls): self.cls = cls
    def _build(self): return self.cls()


def fake_agents():
    return {name: Agent(instructions="Offline test", listen=Factory(FakeSTT),
        think=Factory(FakeLLM), speak=Factory(FakeTTS), turn_taking=SilenceTurnTaking(0))
        for name in ("jev", "silence")}
