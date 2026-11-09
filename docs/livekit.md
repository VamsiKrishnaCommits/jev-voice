# Hosted LiveKit v1 comparison mode

This mode uses the actual hosted `turn-detector-v1` model from LiveKit Agents 1.8.3. It does **not** run a complete LiveKit AgentSession: a Pipecat adapter forwards audio, consumes predictions and controls transcript handoff in our existing pipeline. Do not describe results as a comparison against the entire LiveKit stack.

## Setup

```sh
.venv/bin/python -m pip install -e 'packages/python[webrtc,demo,test,livekit]'
npm run build
```

Place `LIVEKIT_API_KEY` and `LIVEKIT_API_SECRET` in the root `.env`. The demo loads `JEV_ENV_FILE` first, then fills in missing values from root `.env`. The browser never receives these credentials. With credentials configured, `/config` enables the LiveKit button. In synthetic-provider test mode the button is disabled: a fake detector is never advertised as hosted v1.

## Policy and differences

- Mic PCM audio (not transcripts) is streamed to the LiveKit hosted model. The model uses its server-calibrated English threshold; STT supplies the eventual text sent to the LLM.
- Silero VAD uses a 0.55-second stop duration in this mode. Endpoint timing accounts for this already elapsed silence. The other demo modes retain their prior VAD settings.
- A completion prediction permits handoff after a minimum 0.3 seconds of silence; an incomplete prediction delays handoff until 2.5 seconds, unless speech resumes. These are adapter settings inspired by the documented LiveKit defaults, not a reproduction of every LiveKit scheduling detail.
- Prediction timeout is 1.2 seconds in this adapter. A failed/late prediction emits `livekit_error` and uses the 2.5-second bound, explicitly labeled `livekit_unavailable_timeout`.
- `local_fallback=False`: **no v1-mini substitution**. SDK default/degraded outputs lacking verified inference metadata are rejected. This is essential to avoid claiming a cloud success when no model ran.
- Audio predictions are shared across late STT updates within the same pause; new speech invalidates them. Closed sessions release the model socket and HTTP client.
- LiveKit mode uses its own bounded endpointing controller. Neither mode implements explicit timed wait/resume commands.

Traces show `livekit_request`, `livekit_response` (probability, threshold, round-trip and server-inference latency), bounded `hold`, and handoffs with reasons `livekit_yield`, `livekit_max_delay` or `livekit_unavailable_timeout`.

## Verified smoke test (September 28, 2026)

Replayed the previously downloaded public `livekit/eot-bench-data` English example `en__875_82__user_turn_007`, unchanged audio. Probed at 6.85 seconds (inside an annotated unfinished pause) and 13.05 seconds (after the completed turn):

| Probe | Expected | Completion probability | Threshold | Round trip | Server inference |
|---|---|---|---|---|---|
| 6.85s | Hold | 0.0003705 | 0.56 | 1,016ms | 109ms |
| 13.05s | Complete | 0.9940929 | 0.56 | 199ms | 95ms |

Both responses were verified hosted-v1 results, not fallback defaults. These are two diagnostic probes, **not an official benchmark score**, turn-level accuracy measurement or latency average. The first response arrived after the annotated pause ended; a live controller must ignore a stale result after new speech. Network and model latency are separate.

A subsequent full-provider replay through the new adapter produced verified hold/yield predictions (184ms and 287ms round trips), one handoff and 101,728 bytes of synthesized PCM output, with no detector fallback. ElevenLabs misidentified the language of the opening segment and its transcript lagged the audio at handoff. This confirms integration and audio output, not transcription accuracy or complete-response quality. The audio detector and STT can disagree on how much speech has been processed; that remains a pipeline limitation to measure separately.

Rerun using your own supplied PCM16 WAV and probe times:

```sh
.venv/bin/python scripts/test_livekit_v1.py /absolute/path/audio.wav --at 6.85 13.05
```

This calls only the detector. The optional `scripts/test_livekit_pipeline.py` additionally calls real STT/LLM/TTS providers and incurs their charges. Both scripts reject unverified fallback predictions. Neither is a benchmark harness.

Sources: [LiveKit model docs](https://docs.livekit.io/agents/logic/turns/turn-detector/), [SDK API](https://docs.livekit.io/reference/python/livekit/agents/inference/eot/detector.html). Data provenance remains in the original local example's `task.json`; no audio was copied into this repository.
