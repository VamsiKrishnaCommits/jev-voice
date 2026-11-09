from jev.latency import TurnLatencyTracker


def tracker():
    now, events = [0.0], []
    item = TurnLatencyTracker(events.append, clock=lambda: now[0])
    def at(seconds, kind, **extra):
        now[0] = seconds
        item.trace({"kind": kind, "turn": 1, "strategy": "JevTurnTaking", **extra})
    def frame(seconds, kind):
        now[0] = seconds
        item.frame(kind)
    return item, events, at, frame, now


def test_measured_waterfall_preserves_overlap_and_first_chunk_only():
    t, events, at, frame, now = tracker()
    at(0, "speech_started")
    at(.3, "stt_partial")
    at(.31, "jev_request")
    at(.4, "jev_response", decision="hold")
    at(.8, "speech_stopped")
    at(.9, "jev_request")
    at(1.1, "jev_response", decision="yield")
    at(1.1, "handoff", reason="yield")
    # Aggregator clears old playback before forwarding the new context.
    frame(1.11, "interruption")
    frame(1.12, "llm_input")
    frame(1.4, "llm_text")
    frame(1.5, "llm_text")
    frame(1.8, "tts_audio")
    frame(2.0, "tts_audio")
    data = events[-1]
    assert data["first_audio_ms"] - data["speech_end_ms"] == 1000
    assert [(s["stage"], s["start_ms"], s["end_ms"]) for s in data["spans"]] == [
        ("stt", 0, 300), ("jev", 310, 400), ("jev", 900, 1100),
        ("llm", 1120, 1400), ("tts", 1400, 1800)]
    frame(2.1, "playback_stopped")
    at(3, "speech_started", turn=2)
    assert t.turns[1]["status"] == "audio_ready"


def test_cancelled_checks_and_fallback_are_not_counted_as_approvals():
    t, events, at, frame, _ = tracker()
    at(0, "speech_started")
    at(.4, "stt_partial")
    at(.5, "jev_request")
    at(.6, "speech_stopped")
    at(.7, "jev_request")
    at(4.6, "handoff", reason="max_silence_timeout")
    checks = [s for s in events[-1]["spans"] if s["stage"] == "jev"]
    assert [s["status"] for s in checks] == ["cancelled", "cancelled"]
    assert events[-1]["reason"] == "max_silence_timeout"
    frame(4.61, "llm_input")
    frame(4.7, "interruption")
    frame(5, "llm_text")
    frame(6, "tts_audio")
    assert events[-1]["status"] == "interrupted"
    assert events[-1]["first_audio_ms"] is None
    assert events[-1]["spans"][-1]["status"] == "cancelled"


def test_unknown_stt_start_and_silence_baseline_are_explicit():
    t, events, at, frame, _ = tracker()
    at(.5, "stt_final", strategy="SilenceTurnTaking")
    at(.6, "jev_request", strategy="SilenceTurnTaking")
    at(1.3, "jev_response", strategy="SilenceTurnTaking", decision="yield")
    at(1.3, "handoff", strategy="SilenceTurnTaking", reason="yield")
    assert events[-1]["spans"] == [{"stage": "stt", "start_ms": None, "end_ms": 0, "status": "complete"}]
    assert events[-1]["speech_end_ms"] is None


def test_resumed_speech_uses_latest_stop_and_close_marks_incomplete():
    t, events, at, frame, now = tracker()
    at(0, "speech_started")
    at(.5, "stt_partial")
    at(.6, "speech_stopped")
    at(1, "speech_started")
    assert events[-1]["speech_end_ms"] is None
    at(2, "speech_stopped")
    at(2.1, "handoff", reason="yield")
    frame(2.2, "llm_input")
    now[0] = 2.3
    t.close()
    assert events[-1]["status"] == "closed"
    assert events[-1]["spans"][-1]["end_ms"] == 2300
    assert events[-1]["spans"][-1]["status"] == "cancelled"
