"""Server-observed turn waterfall. No content or provider secrets are collected.

STT measures detected speech -> first transcript, not provider compute time.
TTS includes sentence buffering after the first LLM text. Audio ends at the
server's first PCM frame, not the browser speaker. Overlaps are intentional.
"""
import time


class TurnLatencyTracker:
    def __init__(self, emit, clock=time.monotonic):
        self.emit, self.clock = emit, clock
        self.turns = {}
        self.response_turn = None
        self.pending_turn = None
        self.seq = 0

    def _turn(self, turn):
        if turn not in self.turns:
            self.turns[turn] = {"turn": turn, "origin": self.clock(), "status": "listening",
                "strategy": "", "speech_end_ms": None, "handoff_ms": None,
                "first_audio_ms": None, "spans": []}
        return self.turns[turn]

    def _now(self, record):
        return max(0, round((self.clock() - record["origin"]) * 1000))

    def _span(self, record, stage):
        return next((span for span in reversed(record["spans"]) if span["stage"] == stage), None)

    def _start(self, record, stage, **extra):
        span = {"stage": stage, "start_ms": self._now(record), "end_ms": None,
                "status": "pending", **extra}
        record["spans"].append(span)
        return span

    def _finish(self, record, span, status="complete"):
        if span and span["status"] == "pending":
            span.update(end_ms=self._now(record), status=status)

    def _publish(self, record):
        self.seq += 1
        self.emit({"type": "turn_latency", "seq": self.seq,
            **{key: value for key, value in record.items() if key not in {"origin", "spans"}},
            "elapsed_ms": self._now(record), "spans": [dict(span) for span in record["spans"]]})

    def trace(self, event):
        kind = event.get("kind")
        if kind not in {"speech_started", "speech_stopped", "stt_partial", "stt_final",
                        "jev_request", "jev_response", "jev_error", "livekit_request",
                        "livekit_response", "livekit_error", "handoff"}:
            return
        record = self._turn(event["turn"])
        record["strategy"] = event.get("strategy", record["strategy"])
        if kind in {"speech_started", "speech_stopped", "stt_partial", "stt_final"}:
            self._finish(record, self._span(record, "jev"), "cancelled")
        if kind == "speech_started":
            self.interrupt(include_pending=True)
            record["speech_end_ms"] = None
            if self._span(record, "stt") is None:
                self._start(record, "stt")
        elif kind == "speech_stopped":
            record["speech_end_ms"] = self._now(record)
        elif kind in {"stt_partial", "stt_final"}:
            span = self._span(record, "stt")
            if span is None:
                # No observed speech-start signal: do not invent a duration.
                span = self._start(record, "stt", start_ms=None)
            if span["status"] == "pending":
                self._finish(record, span)
            else:
                return
        elif kind in {"jev_request", "livekit_request"}:
            if record["strategy"] == "SilenceTurnTaking":
                return
            self._finish(record, self._span(record, "jev"), "cancelled")
            self._start(record, "jev")
        elif kind in {"jev_response", "livekit_response"}:
            span = self._span(record, "jev")
            self._finish(record, span)
            if span:
                span["decision"] = event.get("decision")
        elif kind in {"jev_error", "livekit_error"}:
            self._finish(record, self._span(record, "jev"), "error")
        elif kind == "handoff":
            self._finish(record, self._span(record, "jev"), "cancelled")
            record.update(status="responding", handoff_ms=self._now(record),
                          reason=event.get("reason"))
            self.pending_turn = event["turn"]
        self._publish(record)

    def frame(self, kind):
        if kind == "interruption":
            self.interrupt()
            return
        if kind == "llm_input" and self.pending_turn is not None:
            # Pipecat can send an interruption to clear the previous response
            # between a handoff and its context frame. Activate only here.
            self.response_turn = self.pending_turn
            self.pending_turn = None
        if self.response_turn is None:
            return
        record = self.turns[self.response_turn]
        if kind == "playback_stopped" and record["first_audio_ms"] is not None:
            self.response_turn = None
            return
        if record["status"] in {"interrupted", "error", "closed"}:
            return
        if kind == "llm_input" and self._span(record, "llm") is None:
            self._start(record, "llm")
        elif kind == "llm_text" and self._span(record, "tts") is None:
            self._finish(record, self._span(record, "llm"))
            self._start(record, "tts")
        elif kind == "tts_audio" and record["first_audio_ms"] is None:
            self._finish(record, self._span(record, "tts"))
            record.update(status="audio_ready", first_audio_ms=self._now(record))
        elif kind == "error":
            for span in record["spans"]:
                self._finish(record, span, "error")
            record["status"] = "error"
        else:
            return
        self._publish(record)

    def interrupt(self, *, include_pending=False):
        if include_pending and self.pending_turn is not None:
            record = self.turns[self.pending_turn]
            record["status"] = "interrupted"
            self.pending_turn = None
            self._publish(record)
        if self.response_turn is None:
            return
        record = self.turns[self.response_turn]
        for span in record["spans"]:
            self._finish(record, span, "cancelled")
        record["status"] = "interrupted"
        self.response_turn = None
        self._publish(record)

    def close(self):
        for record in self.turns.values():
            if record["status"] in {"listening", "responding"}:
                for span in record["spans"]:
                    self._finish(record, span, "cancelled")
                record["status"] = "closed"
                self._publish(record)
