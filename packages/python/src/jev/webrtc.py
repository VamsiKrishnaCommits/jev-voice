"""FastAPI signaling adapter. Native media goes over WebRTC, not HTTP.

Own this server in an application lifespan and call close() at shutdown.
Session tokens authorize offers, trace polling and deletion. authenticate is an
optional async application hook for session creation; default is local-only.
"""
import asyncio
import logging
import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field


class SessionRequest(BaseModel):
    agent: str
    debug: bool = False


class Offer(BaseModel):
    type: Literal["offer"]
    sdp: str = Field(min_length=1, max_length=100_000)


@dataclass
class Session:
    id: str
    token: str
    agent: object
    debug: bool
    created: float = field(default_factory=time.monotonic)
    pc: object = None
    task: object = None
    runner: object = None
    offering: bool = False
    seq: int = 0
    events: deque = field(default_factory=lambda: deque(maxlen=500))
    latency: object = None


class VoiceServer:
    def __init__(self, agents, *, ice_servers=(), authenticate=None, max_sessions=8,
                 session_seconds=600, offer_seconds=30):
        self.agents = dict(agents)
        self.ice_servers = list(ice_servers)
        self.authenticate = authenticate
        self.max_sessions = max_sessions
        self.session_seconds, self.offer_seconds = session_seconds, offer_seconds
        self.sessions = {}
        self.sweeper = None
        self.closed = False
        self.router = APIRouter()
        self.router.add_api_route("/sessions", self.create, methods=["POST"])
        self.router.add_api_route("/sessions/{session_id}/offer", self.offer, methods=["POST"])
        self.router.add_api_route("/sessions/{session_id}/events", self.events, methods=["GET"])
        self.router.add_api_route("/sessions/{session_id}", self.delete, methods=["DELETE"])

    def authorized(self, session_id, request):
        session = self.sessions.get(session_id)
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        if session is None or not secrets.compare_digest(session.token, token):
            raise HTTPException(404, "Session not found")
        return session

    async def create(self, body: SessionRequest, request: Request):
        if self.closed:
            raise HTTPException(503, "Server shutting down")
        if self.authenticate:
            await self.authenticate(request)
        else:
            if not request.client or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
                raise HTTPException(403, "Configure authenticate for non-local clients")
            origin = request.headers.get("origin")
            if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
                raise HTTPException(403, "Cross-origin session creation is disabled")
        if body.agent not in self.agents:
            raise HTTPException(404, "Unknown agent")
        if len(self.sessions) >= self.max_sessions:
            raise HTTPException(429, "Session limit reached")
        session = Session(str(uuid4()), secrets.token_urlsafe(32), self.agents[body.agent], body.debug)
        self.sessions[session.id] = session
        if self.sweeper is None:
            self.sweeper = asyncio.create_task(self._sweep())
        return {"session_id": session.id, "token": session.token,
                "ice_servers": self.ice_servers, "expires_in": self.session_seconds,
                "protocol_version": 1}

    async def offer(self, session_id: str, body: Offer, request: Request):
        session = self.authorized(session_id, request)
        if session.offering:
            raise HTTPException(409, "Session already negotiated; create a new session to reconnect")
        session.offering = True
        from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection, IceServer
        try:
            lines = body.sdp.splitlines()
            if not lines or lines[0] != "v=0" or not any(line.startswith("m=audio ") for line in lines):
                raise ValueError("An audio SDP offer is required")
            session.pc = SmallWebRTCConnection(ice_servers=[IceServer(**x) for x in self.ice_servers] or None)
            async with asyncio.timeout(20):
                await session.pc.initialize(sdp=body.sdp, type=body.type)
            if session.id not in self.sessions:
                await session.pc.disconnect()
                raise ValueError("Session closed during negotiation")
            answer = session.pc.get_answer()
            if not answer:
                raise RuntimeError("No WebRTC answer")
            session.runner = asyncio.create_task(self._run(session))
            return {"type": answer["type"], "sdp": answer["sdp"]}
        except Exception:
            await self._remove(session.id)
            raise HTTPException(400, "Unable to negotiate WebRTC")

    async def events(self, session_id: str, request: Request, after: int = 0):
        session = self.authorized(session_id, request)
        if not session.debug:
            raise HTTPException(403, "Debug was not enabled for this session")
        if after < 0:
            raise HTTPException(400, "Invalid cursor")
        first = session.events[0]["seq"] if session.events else session.seq + 1
        return {"cursor": session.seq, "truncated": after < first - 1,
                "events": [x for x in session.events if x["seq"] > after]}

    async def delete(self, session_id: str, request: Request):
        self.authorized(session_id, request)
        await self._remove(session_id)
        return {"closed": True}

    def _record(self, session, event):
        if getattr(session, "latency", None):
            session.latency.trace(event)
        session.seq += 1
        entry = {**event, "seq": session.seq, "elapsedMs": round((time.monotonic()-session.created)*1000)}
        if session.debug:
            session.events.append(entry)
        if event["kind"] in {"speech_started", "jev_request", "jev_response", "jev_error",
                              "livekit_request", "livekit_response", "livekit_error", "handoff"}:
            # Small, immediate status events work even when full debug traces
            # are disabled. Never send model inputs/raw responses here.
            summary = {key: entry[key] for key in ("kind", "strategy", "turn", "revision",
                       "seq", "decision", "latency_ms", "reason", "silence_ms") if key in entry}
            session.pc.send_app_message({"type": "turn_decision", **summary})
        if event["kind"] == "handoff":
            session.pc.send_app_message({"type": "activity", "activity": "thinking"})
        elif event["kind"] == "livekit_error":
            session.pc.send_app_message({"type": "error", "code": "turn_detector_unavailable",
                "message": event["message"], "recoverable": True})

    async def _run(self, session):
        from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
        from pipecat.transports.base_transport import TransportParams
        from pipecat.pipeline.pipeline import Pipeline
        from pipecat.pipeline.task import PipelineTask, PipelineParams
        from pipecat.pipeline.runner import PipelineRunner
        from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
        from pipecat.frames.frames import (TranscriptionFrame, InterimTranscriptionFrame,
            LLMTextFrame, LLMFullResponseStartFrame, LLMFullResponseEndFrame,
            BotStartedSpeakingFrame, BotStoppedSpeakingFrame, InterruptionFrame, ErrorFrame,
            LLMContextFrame, TTSAudioRawFrame)
        from jev_pipecat.agent import ApprovedTranscript
        from .latency import TurnLatencyTracker
        send = session.pc.send_app_message
        session.latency = TurnLatencyTracker(send)
        seen = set()
        class Events(FrameProcessor):
            async def process_frame(self, frame, direction):
                await super().process_frame(frame, direction)
                if frame.id not in seen:
                    seen.add(frame.id)
                    if len(seen) > 4096:
                        seen.clear()
                    if direction == FrameDirection.DOWNSTREAM:
                        if isinstance(frame, LLMContextFrame):
                            session.latency.frame("llm_input")
                        elif isinstance(frame, LLMTextFrame) and frame.text.strip():
                            session.latency.frame("llm_text")
                        elif isinstance(frame, TTSAudioRawFrame) and frame.audio:
                            session.latency.frame("tts_audio")
                    # The upstream copy can arrive after the next context;
                    # only the downstream playback-clear boundary owns timing.
                    if isinstance(frame, InterruptionFrame) and direction == FrameDirection.DOWNSTREAM:
                        session.latency.frame("interruption")
                    elif isinstance(frame, ErrorFrame):
                        session.latency.frame("error")
                    elif isinstance(frame, BotStoppedSpeakingFrame):
                        session.latency.frame("playback_stopped")
                    if isinstance(frame, (TranscriptionFrame, InterimTranscriptionFrame)) and not isinstance(frame, ApprovedTranscript):
                        send({"type": "transcript", "speaker": "user", "text": frame.text,
                              "final": isinstance(frame, TranscriptionFrame)})
                    elif isinstance(frame, LLMTextFrame):
                        send({"type": "transcript", "speaker": "assistant", "text": frame.text, "final": False, "delta": True})
                    elif isinstance(frame, LLMFullResponseStartFrame):
                        send({"type": "response_start"})
                    elif isinstance(frame, LLMFullResponseEndFrame):
                        send({"type": "response_end"})
                    elif isinstance(frame, BotStartedSpeakingFrame):
                        send({"type": "activity", "activity": "speaking"})
                    elif isinstance(frame, BotStoppedSpeakingFrame):
                        send({"type": "activity", "activity": "listening"})
                    elif isinstance(frame, InterruptionFrame):
                        send({"type": "interruption"})
                        send({"type": "activity", "activity": "listening"})
                    elif isinstance(frame, ErrorFrame):
                        send({"type": "error", "code": "provider_error", "message": "A voice provider failed. See server logs.", "recoverable": False})
                await self.push_frame(frame, direction)
        try:
            transport = SmallWebRTCTransport(session.pc, TransportParams(audio_in_enabled=True, audio_out_enabled=True))
            stt, gate, pair, llm, tts = session.agent._components(on_trace=lambda event: self._record(session, event))
            session.task = PipelineTask(Pipeline([transport.input(), stt, Events(), gate, pair.user(), Events(), llm,
                Events(), tts, Events(), transport.output(), pair.assistant()]), enable_rtvi=False,
                params=PipelineParams(enable_metrics=True))
            @transport.event_handler("on_client_disconnected")
            async def disconnected(transport, client):
                await session.task.cancel()
            @transport.event_handler("on_client_connected")
            async def connected(transport, client):
                send({"type": "activity", "activity": "listening"})
            await PipelineRunner(handle_sigint=False).run(session.task)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logging.getLogger(__name__).exception("Voice pipeline failed for session %s", session.id)
            self._record(session, {"kind": "session_error", "error": type(error).__name__})
            send({"type": "error", "code": "session_error", "message": "Voice session failed; check server configuration.", "recoverable": False})
        finally:
            session.latency.close()
            await self._remove(session.id)

    async def _remove(self, session_id):
        session = self.sessions.pop(session_id, None)
        if session is None:
            return
        if session.task:
            await session.task.cancel()
        if session.pc:
            await session.pc.disconnect()
        if session.runner and session.runner is not asyncio.current_task():
            try:
                async with asyncio.timeout(10):
                    await session.runner
            except TimeoutError:
                session.runner.cancel()
                await asyncio.gather(session.runner, return_exceptions=True)

    async def _sweep(self):
        while True:
            await asyncio.sleep(5)
            for session in list(self.sessions.values()):
                age = time.monotonic() - session.created
                if age > self.session_seconds or (not session.offering and age > self.offer_seconds):
                    await self._remove(session.id)

    async def close(self):
        self.closed = True
        if self.sweeper:
            self.sweeper.cancel()
            await asyncio.gather(self.sweeper, return_exceptions=True)
        for session_id in list(self.sessions):
            await self._remove(session_id)
