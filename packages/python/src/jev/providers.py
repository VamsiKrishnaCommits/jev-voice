"""Provider configuration. Connections are created per conversation."""
import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Provider:
    kind: str
    model: str
    voice: str | None = None
    api_key: str | None = field(default=None, repr=False)
    reasoning_effort: str | None = None

    def _build(self):
        if self.kind == "llm":
            from pipecat.services.openai.llm import OpenAILLMService
            key = self.api_key or os.getenv("OPENAI_API_KEY")
            if not key:
                raise ValueError("OPENAI_API_KEY is required")
            extra = {"reasoning_effort": self.reasoning_effort} if self.reasoning_effort is not None else {}
            return OpenAILLMService(api_key=key, settings=OpenAILLMService.Settings(model=self.model, extra=extra))
        key = self.api_key or os.getenv("ELEVENLABS_API_KEY") or os.getenv("ELEVEN_LABS")
        if not key:
            raise ValueError("ELEVENLABS_API_KEY is required")
        if self.kind == "stt":
            from pipecat.services.elevenlabs.stt import ElevenLabsRealtimeSTTService
            return ElevenLabsRealtimeSTTService(api_key=key,
                settings=ElevenLabsRealtimeSTTService.Settings(model=self.model))
        voice = self.voice or os.getenv("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb")
        if self.model.startswith(("eleven_v3", "eleven_v4")):
            # Dialogue models use a different WebSocket protocol from Flash.
            # Reuse Pipecat's per-turn contexts: late audio from an interrupted
            # context is discarded rather than leaking into the next response.
            from pipecat.services.elevenlabs.dialogue.tts import ElevenLabsDialogueTTSService
            return ElevenLabsDialogueTTSService(api_key=key,
                settings=ElevenLabsDialogueTTSService.Settings(
                    voice=voice, model=self.model, stability=0.5))
        from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
        return ElevenLabsTTSService(api_key=key, settings=ElevenLabsTTSService.Settings(
            voice=voice, model=self.model))


class elevenlabs:
    @staticmethod
    def stt(model="scribe_v2_realtime", *, api_key=None):
        return Provider("stt", model, api_key=api_key)

    @staticmethod
    def tts(voice=None, model="eleven_flash_v2_5", *, api_key=None):
        return Provider("tts", model, voice, api_key)


class openai:
    @staticmethod
    def llm(model="gpt-4.1-mini", *, api_key=None, reasoning_effort=None):
        return Provider("llm", model, api_key=api_key, reasoning_effort=reasoning_effort)
