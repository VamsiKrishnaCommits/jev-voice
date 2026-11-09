"""Hosted-only smoke test on a supplied PCM16 WAV. No LLM, STT, or TTS calls.

Usage: python scripts/test_livekit_v1.py path/to/audio.wav --at 6.85 13.05
Prediction timestamps are diagnostic probes, not official benchmark grading.
"""
import argparse
import asyncio
import json
import time
import wave
from pathlib import Path

import aiohttp
from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import inference, APIConnectOptions
from livekit.agents.language import LanguageCode


async def run(path, probes):
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    with wave.open(str(path), "rb") as source:
        rate, channels, width = source.getframerate(), source.getnchannels(), source.getsampwidth()
        assert width == 2, "Expected PCM16 WAV"
        pcm = source.readframes(source.getnframes())
    async with aiohttp.ClientSession() as http:
        options = APIConnectOptions(max_retry=0, timeout=10)
        detector = inference.TurnDetector(version="v1", local_fallback=False, http_session=http)
        stream = detector.stream(conn_options=options)
        try:
            # Wait for calibrated gateway thresholds: proves initialization, not just a local object.
            async with asyncio.timeout(12):
                while await stream.unlikely_threshold(LanguageCode("en")) is None:
                    if stream.is_degraded:
                        raise RuntimeError("Hosted v1 unavailable; no local fallback was used")
                    await asyncio.sleep(.05)
            started = time.monotonic()

            async def feed():
                chunk_samples = rate // 50
                chunk_bytes = chunk_samples * channels * 2
                for offset in range(0, len(pcm), chunk_bytes):
                    data = pcm[offset:offset+chunk_bytes]
                    stream.push_audio(rtc.AudioFrame(data, rate, channels, len(data)//(2*channels)))
                    await asyncio.sleep(max(0, started + (offset+len(data))/(rate*channels*2) - time.monotonic()))

            feeder = asyncio.create_task(feed())
            results = []
            try:
                for at in probes:
                    await asyncio.sleep(max(0, started+at-time.monotonic()))
                    request_at = time.monotonic()
                    event = await asyncio.wait_for(stream.predict(), timeout=5)
                    if stream.is_degraded or stream.is_fallback or event.inference_duration is None:
                        raise RuntimeError("No verified hosted-model prediction; refusing to count default/fallback output")
                    threshold = await stream.unlikely_threshold(LanguageCode("en"))
                    result = {"model": stream.model, "audio_seconds": at,
                        "end_of_turn_probability": event.end_of_turn_probability,
                        "threshold": threshold, "complete": event.end_of_turn_probability >= threshold,
                        "roundtrip_ms": round((time.monotonic()-request_at)*1000),
                        "server_inference_ms": round(event.inference_duration*1000), "fallback": stream.is_fallback}
                    results.append(result)
                    print(json.dumps(result), flush=True)
                await feeder
            finally:
                feeder.cancel()
                await asyncio.gather(feeder, return_exceptions=True)
            return results
        finally:
            await stream.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("wav", type=Path)
    parser.add_argument("--at", type=float, nargs="+", required=True)
    args = parser.parse_args()
    asyncio.run(run(args.wav, args.at))
