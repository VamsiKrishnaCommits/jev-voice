"""Provider-independent turn ownership. No Pipecat imports."""
import asyncio
import math
import re
import time
from dataclasses import dataclass


@dataclass
class TurnPolicy:
    uncertain_seconds: float = 1.5
    debounce_seconds: float = .12
    recheck_seconds: tuple = (.6, 1, 1)
    max_silence_seconds: float = 4

    def __post_init__(self):
        values = (self.uncertain_seconds, self.debounce_seconds, *self.recheck_seconds)
        if (any(not math.isfinite(v) or v < 0 for v in values)
                or not math.isfinite(self.max_silence_seconds) or self.max_silence_seconds <= 0):
            raise ValueError("Turn timings must be finite and nonnegative; max silence must be positive")


class JevTurnController:
    def __init__(self, client, *, on_yield, context=lambda: [], trace=lambda event: None, policy=None):
        self.client, self.on_yield, self.context, self.trace = client, on_yield, context, trace
        self.policy = policy or TurnPolicy()
        self.final = self.partial = ""
        self.speaking = False
        self.revision = 0
        self.turn = 1
        self.task = None
        self.decision = None
        self.silence_task = None
        self.silence_epoch = 0
        self.silence_at = None
        self.late_final = False
        self.consumed_partial = ""
        self.closed = False

    @property
    def text(self):
        return " ".join(filter(None, [self.final, self.partial])).strip()

    def emit(self, kind, **data):
        self.trace({"kind": kind, "turn": self.turn, "revision": self.revision, **data})

    def invalidate(self):
        self.revision += 1
        if self.task and self.task is not asyncio.current_task():
            self.task.cancel()
        self.task = None

    def speech_started(self):
        if self.closed:
            return
        self.speaking = True
        self._cancel_silence_limit()
        self.silence_at = None
        self.decision = None
        self.invalidate()
        self.emit("speech_started")

    def speech_stopped(self):
        if self.closed:
            return
        if self.speaking or self.silence_at is None:
            self.silence_at = time.monotonic()
        self.speaking = False
        self.emit("speech_stopped")
        self.schedule()

    def transcript(self, text, *, final=False):
        if not text.strip() or self.closed:
            return
        # Remove only the consumed prefix, never a whole growing segment.
        if self.late_final:
            old = re.findall(r"\w+", self.consumed_partial.lower())
            matches = list(re.finditer(r"\w+", text))
            current = [m.group().lower() for m in matches]
            if current[:len(old)] == old and old:
                text = text[matches[len(old)-1].end():].lstrip(" .,!?;:-")
            elif current == old:
                text = ""
            if final:
                self.late_final = False
                self.consumed_partial = ""
            if not re.search(r"\w", text):
                self.emit("stt_previous_segment_ignored", text=text, final=final)
                return
        if final:
            self.final = " ".join(filter(None, [self.final, text])).strip()
            self.partial = ""
        else:
            self.partial = text
        self.emit("stt_final" if final else "stt_partial", text=text)
        self.schedule()

    def schedule(self):
        self.invalidate()
        if self.text and not self.closed:
            if not self.speaking and self.silence_at is None:
                self.silence_at = time.monotonic()
            self._ensure_silence_limit()
            self.task = asyncio.create_task(self._evaluate(self.revision))

    def _cancel_silence_limit(self):
        self.silence_epoch += 1
        if self.silence_task and self.silence_task is not asyncio.current_task():
            self.silence_task.cancel()
        self.silence_task = None

    def _ensure_silence_limit(self):
        # Independent of transcript revisions and model latency. Rechecks/finals
        # cannot postpone the deadline; only actual resumed speech starts anew.
        if self.speaking or self.silence_task is not None:
            return
        self.silence_task = asyncio.create_task(self._silence_limit(self.silence_epoch))

    async def _silence_limit(self, epoch):
        deadline = self.silence_at + self.policy.max_silence_seconds
        await asyncio.sleep(max(0, deadline - time.monotonic()))
        if epoch == self.silence_epoch and not self.speaking and not self.closed:
            self.emit("silence_limit", seconds=self.policy.max_silence_seconds,
                      last_decision=self.decision, model_approved=False)
            await self.release("max_silence_timeout")

    async def _evaluate(self, revision):
        try:
            await asyncio.sleep(self.policy.debounce_seconds)
            for attempt in range(len(self.policy.recheck_seconds) + 1):
                text = self.text
                state = {"text": text, "context": self.context(),
                         "timing": {"speaking": self.speaking, "silenceMs": 0 if self.speaking else round((time.monotonic() - self.silence_at) * 1000)}}
                self.emit("jev_request", attempt=attempt, **state)
                result = await self.client.evaluate_turn(**state)
                if revision != self.revision or self.closed:
                    return
                self.emit("jev_response", **result)
                self.decision = result["decision"]
                if self.speaking:
                    return
                if self.decision == "yield":
                    await self.release("yield")
                    return
                if self.decision == "uncertain":
                    remaining = self.policy.uncertain_seconds - (time.monotonic() - self.silence_at)
                    await asyncio.sleep(max(0, remaining))
                    if revision == self.revision:
                        await self.release("uncertain_timeout")
                    return
                self.emit("hold", recheck=attempt, bounded=True,
                          remaining_ms=max(0, round((self.silence_at + self.policy.max_silence_seconds - time.monotonic()) * 1000)))
                if attempt < len(self.policy.recheck_seconds):
                    await asyncio.sleep(self.policy.recheck_seconds[attempt])
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if revision == self.revision and not self.closed:
                self.emit("jev_error", error=type(error).__name__, policy="bounded_silence_fallback")

    async def release(self, reason="manual"):
        if self.speaking or not self.text or self.closed:
            return
        text = self.text
        self.late_final = bool(self.partial)
        self.consumed_partial = self.partial
        self.invalidate()
        self._cancel_silence_limit()
        self.emit("handoff", text=text, reason=reason, silence_ms=round((time.monotonic() - self.silence_at) * 1000))
        self.final = self.partial = ""
        self.decision = None
        self.silence_at = None
        self.turn += 1
        await self.on_yield(text)

    async def close(self):
        self.closed = True
        tasks = [task for task in (self.task, self.silence_task) if task and task is not asyncio.current_task()]
        self.invalidate()
        self._cancel_silence_limit()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
