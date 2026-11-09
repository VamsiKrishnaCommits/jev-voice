# Expressive voice test

The demo selects `eleven_v4_turbo` by default and shows its model ID below the
connection status. All turn-taking modes share the same TTS model and response
instructions. No automatic Flash fallback:
a v4 failure must be visible, not mistaken for a successful expressive test.

For an opt-in paid smoke test of the actual LLM → TTS adapters, including a
response after interruption (no microphone or STT/Jev evaluation):

```sh
JEV_ENV_FILE=../Jev/.env .venv/bin/python scripts/test_expressive_pipeline.py
```

## Run

From the monorepo root, with valid provider credentials in `.env` or the indicated
external env file:

```sh
npm run build
JEV_ENV_FILE=../Jev/.env .venv/bin/python -m uvicorn app:app --app-dir apps/demo/server --host 127.0.0.1 --port 7870
```

Open http://127.0.0.1:7870/, choose Jev, and start a conversation. Use headphones
to avoid speaker feedback triggering an interruption. The voice still streams;
the adapter aggregates complete sentences before sending to ElevenLabs.

## The midnight museum

Read the quoted speech. Bracketed pause instructions below are actions for you,
not text to speak. Responses are generated, not scripted or guaranteed.

1. **Whisper:** “Let's role-play. You're my partner in a fictional museum mystery.
   Keep replies short and react naturally. We've just heard footsteps. Whisper:
   what do we do?”
2. **Unfinished thought:** “I found something behind the painting, but…”
   **[pause three seconds]** “…it's not the key. It's a note with my name on it.
   What do you think it means?”
3. **Thinking pause:** “The note says…” **[pause two seconds]** “…meet me by
   the north staircase. Should we go?” Jev should allow the unfinished pause,
   then hand off the completed question. No exact-duration wait commands are
   supported. If you trail off entirely, a 4-second detected-silence ceiling
   hands the fragment to the assistant for a short clarification.
4. **Change of mood:** “It's an invitation! The guard arranged a surprise birthday
   party. React like you're delighted.”
5. **Barge in:** While it is speaking, say: “Actually, stop. Drop the character
   and tell me what happened in one calm sentence.”

Listen for whisper → curiosity → excitement → calm, appropriate rather than
constant theatrical delivery, no spoken tag names, and no old audio resurfacing
after interruption. In “Behind the pause”, check Jev's hold/handoff events.
Repeat the unfinished thought in silence mode to compare timing fairly.

For Flash comparison, restart with `ELEVENLABS_TTS_MODEL=eleven_flash_v2_5`.
This compares the old plain-speech setup with v4 plus expressive prompting, not
a controlled model-only voice-quality benchmark. Real provider tests cost money.

## SDK usage

```python
from jev.prompts import EXPRESSIVE_VOICE_INSTRUCTIONS
from jev.providers import elevenlabs

speak = elevenlabs.tts(model="eleven_v4_turbo")
instructions = "Be a concise, helpful assistant.\n" + EXPRESSIVE_VOICE_INSTRUCTIONS
# Pass these as Agent(speak=speak, instructions=instructions, ...).
```

The prompt permits a small set of tags and limits them to at most two in an
explicit performance. Tags remain available in the raw transcript for inspection.
