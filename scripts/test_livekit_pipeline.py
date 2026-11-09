"""Opt-in live-provider integration test. Sends the supplied public WAV to providers.

Uses paid STT/LLM/TTS calls. Reads JEV_ENV_FILE plus the local .env. Not a benchmark.
"""
import argparse
import asyncio
import json
import os
import time
import wave
from pathlib import Path
from dotenv import load_dotenv
from jev import Agent, Audio
from jev.livekit import LiveKitTurnTaking
from jev.providers import elevenlabs, openai


async def run(path):
    root = Path(__file__).resolve().parents[1]
    load_dotenv(os.getenv("JEV_ENV_FILE", root / ".env"))
    load_dotenv(root / ".env")
    with wave.open(str(path), "rb") as source:
        rate = source.getframerate()
        assert source.getnchannels() == 1 and source.getsampwidth() == 2
        pcm = source.readframes(source.getnframes())
    events = []
    agent = Agent(instructions="Reply in one short sentence.", listen=elevenlabs.stt(),
        think=openai.llm(), speak=elevenlabs.tts(), turn_taking=LiveKitTurnTaking(),
        trace=lambda: events.append)
    async def input_audio():
        # Allow provider connections to initialize, then replay in real time.
        await asyncio.sleep(2)
        started = time.monotonic()
        audio = pcm + bytes(rate * 2 * 10)
        for offset in range(0, len(audio), rate // 50 * 2):
            chunk = audio[offset:offset+rate//50*2]
            yield chunk
            await asyncio.sleep(max(0, started + (offset+len(chunk))/(rate*2)-time.monotonic()))
    audio_bytes = 0
    async with asyncio.timeout(45):
        async with agent.conversation(sample_rate=rate) as conversation:
            async for event in conversation.stream(input_audio()):
                if isinstance(event, Audio):
                    audio_bytes += len(event.data)
    result = {"output_audio_bytes": audio_bytes,
        "predictions": [e for e in events if e["kind"] == "livekit_response"],
        "handoffs": [{"reason":e["reason"], "silence_ms":e["silence_ms"]} for e in events if e["kind"] == "handoff"],
        "errors": [e for e in events if e["kind"] == "livekit_error"]}
    print(json.dumps(result, indent=2))
    assert audio_bytes > 0 and result["predictions"], "No verified hosted prediction plus audio output"
    assert not result["errors"], "Detector fallback occurred; test is not a clean hosted-model result"

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("wav", type=Path)
    asyncio.run(run(parser.parse_args().wav))
