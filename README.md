# Jev Voice

When I saw how low Jev's latency was, I realized I could place it between speech-to-text (STT) and the LLM to decide when to wait and when to respond. A pause doesn't always mean someone has finished their thought—and handing off too early makes a voice assistant interrupt them.

The results in my testing were surprisingly good: fewer premature interruptions and more natural conversations. This repo turns that experiment into a Python voice-agent SDK, a TypeScript browser client, and a local playground so you can try it yourself.

The idea is simple: **STT → Jev turn-taking gate → LLM → TTS**. Jev can hold an unfinished thought, yield a completed turn, or signal uncertainty; the controller keeps waiting bounded so the conversation doesn't hang indefinitely. This gate decides when to hand the user's words to the LLM—stopping assistant audio when the user speaks again is handled separately by the pipeline.

The included demo uses **both packages**, with a switch between Jev turn-taking and a fixed silence timeout while keeping the response providers the same. Try the same words and pauses in each mode, and inspect the decisions and per-turn latency. These are observations from an experiment, not a claim of benchmark superiority.

This is an unpublished development SDK. It uses Pipecat internally for streaming providers, audio processing and WebRTC; it does not fork Pipecat. No Pipecat UI or client is required. Provider calls incur charges.

## Layout

```
packages/python/       jev-voice: Agent, turn-taking strategies, VoiceServer
packages/typescript/   @jev/voice: browser VoiceClient and typed callbacks
apps/demo/server/      FastAPI app, two otherwise identical agents
apps/demo/web/         Custom comparison UI consuming @jev/voice
tests/browser/         Real WebRTC transport test with synthetic providers
docs/                  Protocol and demo guide
```

## Run locally

Requires Python 3.11–3.13 (tested with 3.12), Node.js 22+, and a browser with microphone/WebRTC support.

Clone and install from source (nothing is published to npm or PyPI):

```sh
git clone https://github.com/VamsiKrishnaCommits/jev-voice.git
cd jev-voice
npm run setup
cp .env.example .env
```

Create `.env` using `.env.example` as the template, with `ELEVENLABS_API_KEY`, `OPENAI_API_KEY` and `TYPESAFE_API_KEY`. Alternatively, set `JEV_ENV_FILE` to an existing env file; no keys need to be copied into this repository.

```sh
npm run demo
```

Open **http://127.0.0.1:7870/** and click Start a conversation. The launcher builds the UI before starting the server. Changing modes ends the current conversation; starting again creates fresh history. Use headphones to avoid speaker echo. Set `PORT` to change the default port, or `PYTHON` during setup to choose your Python executable.

**No keys yet?** Run `npm run demo:offline` instead. This tests the real WebRTC connection with synthetic transcripts and a test tone—not real speech recognition, Jev decisions, or generated speech. It makes no paid provider calls. The UI clearly labels this mode.

## Try a natural pause

Read the exact same script in each mode; bracketed text is a silent stage direction:

> “Help me write a message to my manager about…” **[pause 2 seconds]** “…moving our meeting to Thursday. Keep it friendly.”

Then try a correction:

> “I need a hotel in Paris…” **[pause 2 seconds]** “…actually, not Paris—Lyon, for two nights.”

Watch the live `hold` / `yield` / `uncertain` decision and the per-turn STT → Jev → LLM → TTS waterfall. Also try a complete question without a long pause: “What is the capital of France?” Jev should not delay every turn indiscriminately. These are illustrative checks, not guaranteed outcomes or a benchmark. [More scenarios and comparison limitations](docs/demo.md).

## Providers and turn-taking

Both agents use ElevenLabs Scribe STT → turn controller → the same response LLM → ElevenLabs v4 Turbo TTS. Only the turn-taking strategy differs. The demo defaults to `eleven_v4_turbo` and adds restrained expressive audio-tag instructions to the LLM. Set `ELEVENLABS_TTS_MODEL=eleven_flash_v2_5` before starting the server to restore Flash (without audio-tag instructions). The SDK's `elevenlabs.tts()` default remains Flash for backward compatibility; explicitly pass `model="eleven_v4_turbo"` to opt in. See [the expressive test scenario](docs/expressive-voice.md).

The demo uses `gpt-6-luna` with `reasoning_effort="none"` for low-latency streamed responses. Set `VOICE_LLM_MODEL=gpt-4.1-mini` to restore the previous model. The SDK default is unchanged; opt in with `openai.llm("gpt-6-luna", reasoning_effort="none")`.

v3/v4 models use Pipecat's Text-to-Dialogue multi-context WebSocket adapter; Flash uses its original TTS adapter. Each interrupted turn's late audio is discarded. Sentence-level text aggregation is retained to avoid splitting audio tags. The pinned Pipecat version may log an outdated “requires an eleven_v3 model” warning for v4; that check is a warning, not a model substitution. Provider availability must be verified with a live test.

- **Jev:** semantic `hold`, `yield`, or `uncertain`, with contextual pause/recheck handling. Yield hands off immediately; uncertain uses a 1.5-second detected-silence budget. A separate 4-second silence ceiling prevents holds or stalled Jev calls from hanging indefinitely. Only resumed speech resets that ceiling. Exact-duration wait commands are not supported.
- **Silence:** release after 0.8 seconds of detected silence. VAD/STT scheduling can add latency.

An optional **LiveKit v1** mode now uses the actual hosted audio detector with a bounded endpointing adapter. Enable it with the `livekit` Python extra and server-side credentials. This compares a model inside our pipeline, **not LiveKit's full stack**. The silence mode is still only a fixed-timeout baseline. See [LiveKit setup and limitations](docs/livekit.md) and [the demo script](docs/demo.md).

## Python: define and expose an agent

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from jev import Agent, JevTurnTaking, SilenceTurnTaking
from jev.providers import elevenlabs, openai
from jev.webrtc import VoiceServer

agent = Agent(
    instructions="Be a helpful, concise voice assistant.",
    listen=elevenlabs.stt(),
    think=openai.llm(),
    speak=elevenlabs.tts(),
    turn_taking=JevTurnTaking(
        instructions="Allow thinking pauses in unfinished lists and corrections."
    ),
)
voice = VoiceServer({"assistant": agent})

@asynccontextmanager
async def lifespan(app):
    yield
    await voice.close()

app = FastAPI(lifespan=lifespan)
app.include_router(voice.router, prefix="/voice")
```

Replace `JevTurnTaking(...)` with `SilenceTurnTaking(seconds=0.8)` for a non-semantic baseline. Strategies implement `create()` and `async decide(turn) -> Decision`; see [Python API](packages/python/README.md).

## TypeScript: connect from your own UI

```ts
import { VoiceClient } from "@jev/voice";

const voice = new VoiceClient({ endpoint: "/voice", agent: "assistant" });
voice.on("state", ({ state }) => console.log(state));
voice.on("transcript", ({ speaker, text, final, delta }) => {
  // User partials replace the in-progress segment; assistant deltas append.
  console.log({ speaker, text, final, delta });
});
voice.on("activity", ({ activity }) => console.log(activity));
voice.on("interruption", () => console.log("Assistant interrupted"));
voice.on("error", error => console.error(error));

// Run connect() inside a user click handler to request microphone permission.
await voice.connect();
voice.setMuted(true);
await voice.disconnect();
// await voice.dispose(); // Also removes listeners and owned audio element.
```

These packages are **not on registries**. The demo resolves TypeScript through npm workspaces and Python through an editable local install. Outside this monorepo, build local wheel/tarball artifacts rather than installing a similarly named registry package.

## Tests

```sh
.venv/bin/python -m pytest packages/python/tests -q
npm run typecheck
npm test
npm run build
npx playwright install chromium
npm run test:e2e
```

The browser test launches its own server on 7871 and Chrome with a synthetic microphone. On macOS it uses system Chrome when available, otherwise Playwright's Chromium; `CHROME_PATH` overrides this. Linux may require `npx playwright install --with-deps chromium`. It verifies signaling, media flow, transcript callbacks, traces, mute, teardown and mode switching, **not real provider accuracy or Jev quality**. No provider keys are needed for these tests. `JEV_DEMO_FAKE=1` explicitly enables synthetic providers and labels the UI accordingly.

## Privacy and deployment limits

- Keys are server-side only. Neither `.env` nor conversation traces belong in Git.
- The demo writes full, unredacted conversation/decision JSONL under `.traces/`. Debug polling is opt-in per session and retains 500 events in memory. Files have no automatic retention policy; manage them before sharing/deploying.
- Local-only by default: same-origin session creation, 8 concurrent sessions, 30-second pending-offer expiry and 10-minute total session lifetime. Expiry is checked every 5 seconds.
- For public deployments provide `VoiceServer(authenticate=...)`, rate limits, explicit trusted-origin policy, HTTPS, TURN/STUN, and appropriately restricted network access. **Do not put the default loopback-only auth behind a public reverse proxy.**
- Authentication hooks must raise an HTTP error on denial. Session bearer tokens authorize offers, debug reads and deletion. Do not log them. Debug authorization/retention may need stricter application policy.
- This initial transport uses complete ICE offers, one offer per session, and explicit reconnect with new history; it has no ICE restart/resume, multi-worker routing, or distributed session store.
- Provider factories currently cover ElevenLabs STT/TTS and the configured LLM. The broader provider-plugin API is not finalized. The inherited raw-PCM API remains available; the demo uses native WebRTC.

See [protocol](docs/protocol.md) and [dependency notes](NOTICE.md). No license has yet been selected for project-authored code. Public visibility does not grant an open-source license; ask the owner before reuse or redistribution beyond rights provided by GitHub's terms.
