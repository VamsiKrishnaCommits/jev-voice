"""Same providers and prompt; only turn-taking changes. Local test server."""
import os
import importlib.util
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from jev import Agent, JevTurnTaking, SilenceTurnTaking, JsonlTrace
from jev.providers import elevenlabs, openai
from jev.prompts import EXPRESSIVE_VOICE_INSTRUCTIONS
from jev.webrtc import VoiceServer

ROOT = Path(__file__).resolve().parents[3]
# Explicit external path lets local testing reuse keys without copying secrets.
load_dotenv(os.getenv("JEV_ENV_FILE", ROOT / ".env"))
load_dotenv(ROOT / ".env")  # Optional local additions, without overriding existing provider keys.
FAKE = os.getenv("JEV_DEMO_FAKE") == "1"

def missing_keys(mode):
    if FAKE:
        return []
    missing = []
    if not (os.getenv("ELEVENLABS_API_KEY") or os.getenv("ELEVEN_LABS")):
        missing.append("ELEVENLABS_API_KEY")
    if not os.getenv("OPENAI_API_KEY"):
        missing.append("OPENAI_API_KEY")
    if mode == "jev" and not os.getenv("TYPESAFE_API_KEY"):
        missing.append("TYPESAFE_API_KEY")
    return missing

def livekit_installed():
    try:
        return importlib.util.find_spec("livekit.agents") is not None
    except ModuleNotFoundError:
        return False

TTS_MODEL = os.getenv("ELEVENLABS_TTS_MODEL", "eleven_v4_turbo")
LLM_MODEL = os.getenv("VOICE_LLM_MODEL", "gpt-6-luna")
LLM_REASONING_EFFORT = "none" if LLM_MODEL == "gpt-6-luna" else None
EXPRESSIVE = TTS_MODEL.startswith(("eleven_v3", "eleven_v4"))
INSTRUCTIONS = ("You are a friendly voice assistant. Answer in one or two sentences. "
                "If the user's thought is incomplete, ask one brief, gentle clarification instead of guessing. "
                "Do not pad replies with automatic fillers. Exact-duration waits and timers are not supported; "
                "never claim to start one. Do not claim to book or purchase anything.")
if EXPRESSIVE:
    INSTRUCTIONS += "\n\n" + EXPRESSIVE_VOICE_INSTRUCTIONS

def agent(mode):
    if mode == "livekit":
        from jev.livekit import LiveKitTurnTaking
        turn_taking = LiveKitTurnTaking()
    else:
        turn_taking = (JevTurnTaking(instructions="Allow pauses in unfinished questions, lists and corrections. Filler sounds alone are not a request for a reply.")
                       if mode == "jev" else SilenceTurnTaking(.8))
    return Agent(
        instructions=INSTRUCTIONS,
        listen=elevenlabs.stt(),
        think=openai.llm(LLM_MODEL, reasoning_effort=LLM_REASONING_EFFORT),
        speak=elevenlabs.tts(model=TTS_MODEL),
        turn_taking=turn_taking,
        trace=lambda: JsonlTrace(ROOT / ".traces" / mode))

agents = {mode: agent(mode) for mode in ("jev", "silence")}
if not FAKE and livekit_installed() and os.getenv("LIVEKIT_API_KEY") and os.getenv("LIVEKIT_API_SECRET"):
    agents["livekit"] = agent("livekit")
if FAKE:
    # Explicit offline test mode, prominently exposed in /config and the UI.
    from fake import fake_agents
    agents = fake_agents()
voice = VoiceServer(agents)

@asynccontextmanager
async def lifespan(app):
    yield
    await voice.close()

app = FastAPI(lifespan=lifespan)
app.include_router(voice.router, prefix="/voice")

@app.get("/config")
async def config():
    return {"fake": FAKE, "silence_seconds": .8,
            "setup": {mode: {"ready": not missing_keys(mode), "missing": missing_keys(mode)} for mode in agents},
            "jev_uncertain_seconds": 1.5, "jev_max_silence_seconds": 4,
            "tts_model": TTS_MODEL, "expressive_voice": EXPRESSIVE,
            "llm_model": LLM_MODEL, "llm_reasoning_effort": LLM_REASONING_EFFORT,
            "modes": list(agents), "livekit_model": "turn-detector-v1" if "livekit" in agents else None}

WEB = ROOT / "apps" / "demo" / "web" / "dist"
if WEB.exists():
    app.mount("/assets", StaticFiles(directory=WEB / "assets"), name="assets")
    @app.get("/")
    async def index():
        return FileResponse(WEB / "index.html")
