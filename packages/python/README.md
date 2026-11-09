# jev-voice (Python)

See the root README for installation and the FastAPI `VoiceServer` example. `Agent` keeps provider configuration separate from per-conversation state. `VoiceServer` handles browser signaling; `Agent.conversation()` supports a lower-level raw PCM stream.

## Implementing turn taking

```python
from jev import Decision, Turn

class MyTurnTaking:
    def create(self):
        return MyTurnTaking()  # Independent state for each conversation.

    async def decide(self, turn: Turn) -> Decision:
        if turn.speaking:
            return Decision("hold")
        # Replace with your model/policy. No duration-sensitive string heuristics needed.
        return Decision("uncertain")
```

Pass an instance as `Agent(turn_taking=MyTurnTaking(), ...)`. `Turn` includes transcript text, conversation history, speaking state and silence duration. `Decision` contains `action` and optional `details`; only `hold`, `yield`, `uncertain` are valid. Timed wait/resume fields and duration parsing have been removed.

`decide` must permit asyncio cancellation; stale results and active speech are guarded by the runtime. `yield` hands off immediately after speech stops. `uncertain` uses `Agent(uncertain_timeout=1.5)`, measured from the start of detected silence. `Agent(max_silence_timeout=4)` bounds all pending decisions, holds and provider failures, independently of model latency. Rechecks and transcript updates cannot extend the deadline; resumed speech cancels it and the next pause starts a fresh budget. No transcript means no automatic handoff. These are handoff budgets, not time-to-audible-response guarantees; STT, LLM and TTS add latency.

The silence strategy also runs under this controller ceiling: if you configure a longer silence baseline, increase `max_silence_timeout` accordingly. The optional LiveKit adapter retains its separate endpointing policy. Legacy `JevTurnStopStrategy` is not used by `Agent`; use the controller-backed `Agent`/`JevVoiceAgent` for this bounded behavior.

Customize Jev with `JevTurnTaking(instructions="...", examples=(...))`. Safety/state management stays in the controller, outside the model prompt.

## Raw PCM

```python
from jev import Audio, ClearAudio

async with agent.conversation(sample_rate=16000) as conversation:
    async for event in conversation.stream(incoming_audio):
        if isinstance(event, Audio):
            await playback.enqueue(event.data, event.sample_rate)
        elif isinstance(event, ClearAudio):
            await playback.clear()
```

`incoming_audio` is an async iterable of mono signed little-endian PCM16 bytes with no WAV header. Keep it open while listening for replies; exhausting input closes the conversation. Consume output promptly. Your custom playback sink must implement cancellation on `ClearAudio`; the native WebRTC demo instead uses transport interruption handling. `playback` above is application-owned pseudocode.
