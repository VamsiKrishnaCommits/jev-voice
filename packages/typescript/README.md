# @jev/voice (browser TypeScript SDK)

Build with `npm run build -w @jev/voice`. Import `VoiceClient` and use callbacks rather than transport frames; see the root README for a complete connection example.

Options: `endpoint`, `agent`, `debug`, `getToken` (async application auth token), and optional `audioElement`. Methods: `connect`, `disconnect`, `dispose`, `setMuted`, `enableAudio`, and `on` (returns an unsubscribe callback).

Events: `state`, `activity`, `transcript`, `response_start`, `response_end`, `interruption`, `turn_decision`, `turn_latency`, `error`, and opt-in `debug`. Types are exported. User partial transcripts replace a segment; assistant deltas append. Activity reports the broad conversation state, not all providers' simultaneous work. Decision and per-turn latency snapshots arrive on the data channel even with debug disabled; latency spans can overlap and missing timestamps are null. See the root protocol guide for measurement definitions.

Browser-only; instantiate after the document body exists and call `connect()` in a user gesture. HTTPS or localhost is required for microphone access. `playback_blocked` means show a button calling `enableAudio()`. `disconnect()` releases the microphone. `dispose()` additionally removes owned DOM/listeners. A new connection starts a new conversation, not a resumed one.

Provider API keys never belong in this client. This package is an unpublished local workspace package, not a registry installation instruction.
