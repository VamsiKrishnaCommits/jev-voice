# WebRTC protocol v1

The example mounts the Python router at `/voice`. HTTP is used for setup and teardown, **not continuous audio upload**.

1. `POST /voice/sessions` with `{"agent":"jev","debug":true}` creates independent conversation state and returns `session_id`, a bearer `token`, `ice_servers`, `expires_in`, and `protocol_version: 1`.
2. The browser captures its microphone, creates an RTCPeerConnection and an ordered `jev-events` data channel, attaches the audio track, creates an SDP offer, and waits for ICE gathering to complete.
3. `POST /voice/sessions/{id}/offer` with `Authorization: Bearer <token>` and `{"type":"offer","sdp":"..."}` returns an SDP answer. Set it as the remote description. One offer per session; create a new session to reconnect.
4. Browser and Python transport establish ICE/DTLS and stream encrypted WebRTC audio in both directions. Candidate addresses/ports are negotiated; media does not flow to an HTTP audio endpoint. TURN may relay media on restricted networks.
5. The data channel carries JSON events: `transcript`, `activity`, `response_start`, `response_end`, `interruption`, `turn_decision`, `turn_latency`, and `error`. The SDK sends a `ping` string every second for the underlying transport's liveness check.
6. Optional `GET /voice/sessions/{id}/events?after=0`, with the same bearer token, returns `{cursor, truncated, events}`. Poll from the returned cursor. This is debug telemetry, not a prerequisite for gating or media. Buffer capacity is 500; `truncated` means older events were evicted.
7. `DELETE /voice/sessions/{id}` with the token cancels the pipeline and closes transport resources. The client also stops mic tracks and detaches playback. Expiry and disconnect detection provide cleanup if the browser disappears.

## Transcript handling

- User STT partials: replace the current in-progress segment.
- User STT finals: finalize that segment. A final segment is **not** necessarily an approved conversation turn.
- Assistant deltas: append until `response_end` or interruption; `response_start` begins a new response.
- Turn decisions and actual LLM handoffs are separate events. Do not infer them from STT finalization. `client.on("turn_decision", callback)` receives compact live updates even with debug disabled: `kind`, `strategy`, `turn`, `revision`, `seq`, and optional `decision`, `latency_ms`, `reason`, `silence_ms`. Raw model payloads remain in optional debug traces. A timeout handoff is not a Jev approval.

## Per-turn latency

`client.on("turn_latency", callback)` receives snapshots without enabling debug traces. Each contains a turn ID, monotonic update sequence, status, strategy, elapsed milliseconds, spans, and nullable speech-end/handoff/first-audio offsets. All offsets share a server monotonic clock relative to the first observed event for that turn. Multiple Jev requests are separate spans; never add overlapping spans to infer end-to-end latency. Old snapshots remain associated with their original turn on interruption.

- STT: detected speech start to first transcript, including speech capture, **not** STT provider compute time. Missing speech-start signals produce a null start/duration.
- Jev: each request to its observed response, cancellation, or error. Silence mode has no Jev span; LiveKit mode labels its detector separately.
- LLM: context frame at LLM input to first nonempty text frame (time to first text, not full response generation).
- TTS: first LLM text to first PCM frame after TTS, including sentence buffering (not full synthesis or browser playback).
- Headline response delay: last detected speech-stop signal to first server audio. Handoff delay is shown separately and includes intentional thinking pauses and detector work.

Pending, cancelled, failed and missing measurements are labeled, not substituted with zero. The demo keeps all turns in the current conversation; starting another conversation clears the chart. These snapshots are live UI telemetry, not a persisted historical analytics API.

## Security and network topology

Default session creation is for a loopback, same-origin development app. Custom async authentication hooks should validate application credentials, origins and permissions and raise `HTTPException` on failure. Browser `getToken` supplies the application bearer token for session creation; later requests use the separate scoped session token. Configure CORS explicitly if serving the client from another origin.

`VoiceServer(ice_servers=[{"urls": "stun:..."}, {"urls": "turn:...", "username": "...", "credential": "..."}])` supplies candidates to both endpoints. Use short-lived TURN credentials. Local demo has no configured ICE servers and is tested on the same machine only. Deploy HTTPS, expose the required WebRTC media ports, and test real NAT/TURN conditions before public use.

In-memory sessions require a single server worker or sticky routing. Provider requests remain server-side. Do not expose full traces to anonymous users in a production service.
