"""Opt-in, paid LLM → expressive TTS integration smoke test (no microphone).

Checks real tagged responses, streamed PCM, interruption and a subsequent reply.
Run from the repository root with JEV_ENV_FILE pointing to your provider keys.
It does not benchmark STT, Jev accuracy, or perceived voice quality.
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

from jev.providers import elevenlabs, openai
from jev.prompts import EXPRESSIVE_VOICE_INSTRUCTIONS


async def main():
    root = Path(__file__).resolve().parents[1]
    load_dotenv(os.getenv("JEV_ENV_FILE", root / ".env"))
    load_dotenv(root / ".env")
    logger.remove()
    logger.add(sys.stderr, level="WARNING")
    from pipecat.frames.frames import (LLMContextFrame, LLMTextFrame,
        TTSAudioRawFrame, TTSStoppedFrame, InterruptionFrame, ErrorFrame,
        BotStoppedSpeakingFrame)
    from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.task import PipelineTask, PipelineParams
    from pipecat.pipeline.runner import PipelineRunner

    model = os.getenv("ELEVENLABS_TTS_MODEL", "eleven_v4_turbo")
    llm_model = os.getenv("VOICE_LLM_MODEL", "gpt-6-luna")
    reasoning_effort = "none" if llm_model == "gpt-6-luna" else None
    results, errors = [], []
    first_audio, finished, interrupted = asyncio.Event(), asyncio.Event(), asyncio.Event()
    current = None
    old_contexts = set()
    reject_old = False

    class CaptureText(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if isinstance(frame, LLMTextFrame) and current is not None:
                current["text"] += frame.text
            await self.push_frame(frame, direction)

    class Sink(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if isinstance(frame, ErrorFrame):
                errors.append(frame.error)
                finished.set()
            elif isinstance(frame, InterruptionFrame):
                interrupted.set()
            elif isinstance(frame, TTSAudioRawFrame) and current is not None:
                if reject_old and frame.context_id in old_contexts:
                    errors.append("Interrupted audio leaked into the new turn")
                current["audio_bytes"] += len(frame.audio)
                current["contexts"].add(frame.context_id)
                if not first_audio.is_set():
                    current["first_audio_ms"] = round((time.monotonic()-current["started"])*1000)
                    print(json.dumps({"stage": "first_audio", "response": len(results),
                        "ms": current["first_audio_ms"]}), flush=True)
                    first_audio.set()
            elif isinstance(frame, TTSStoppedFrame):
                # No physical output transport in this test; emulate its
                # playback-complete signal so TTS can process the next turn.
                await self.push_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
                finished.set()
            await self.push_frame(frame, direction)

    task = PipelineTask(Pipeline([openai.llm(llm_model, reasoning_effort=reasoning_effort)._build(), CaptureText(),
        elevenlabs.tts(model=model)._build(), Sink()]), enable_rtvi=False,
        params=PipelineParams(audio_out_sample_rate=24000))
    runner = asyncio.create_task(PipelineRunner(handle_sigint=False).run(task))

    async def request(prompt):
        nonlocal current
        first_audio.clear()
        finished.clear()
        current = {"text": "", "audio_bytes": 0, "contexts": set(), "started": time.monotonic()}
        results.append(current)
        context = LLMContext([
            {"role": "system", "content": "You are a concise voice assistant.\n" + EXPRESSIVE_VOICE_INSTRUCTIONS},
            {"role": "user", "content": prompt},
        ])
        await task.queue_frame(LLMContextFrame(context))

    try:
        async with asyncio.timeout(60):
            await request("In our fictional museum mystery, whisper one short sentence telling me where to hide.")
            await finished.wait()
            assert not errors, errors
            assert current["audio_bytes"] > 0 and "[whispers]" in current["text"], current

            await request("In our fictional museum mystery, tell me a suspenseful story in six sentences.")
            await first_audio.wait()
            old_contexts.update(current["contexts"])
            await task.queue_frame(InterruptionFrame())
            await interrupted.wait()
            reject_old = True
            await request("Drop the role-play. In one calm sentence, say that you stopped and are listening.")
            await finished.wait()
            assert not errors, errors
            assert current["audio_bytes"] > 0, "No response after interruption"
    except BaseException:
        print(json.dumps({"stage": "failed", "responses": [
            {k: v for k, v in r.items() if k not in ("started", "contexts")} for r in results],
            "errors": errors}), flush=True)
        raise
    finally:
        await task.cancel()
        await runner
    print(json.dumps({"model": model, "llm_model": llm_model, "reasoning_effort": reasoning_effort,
        "interruption_seen": interrupted.is_set(),
        "responses": [{k: v for k, v in r.items() if k not in ("started", "contexts")} for r in results],
        "errors": errors}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
