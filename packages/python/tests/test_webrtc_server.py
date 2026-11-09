import pytest
from httpx import AsyncClient, ASGITransport
from fastapi import FastAPI, HTTPException
from jev.webrtc import VoiceServer


def test_live_decision_is_compact_and_does_not_require_debug():
    from types import SimpleNamespace
    from unittest.mock import Mock
    import time
    server = VoiceServer({"jev": object()})
    session = SimpleNamespace(seq=0, created=time.monotonic(), debug=False,
                              events=[], pc=Mock())
    server._record(session, {"kind": "jev_response", "strategy": "JevTurnTaking",
        "turn": 1, "revision": 2, "decision": "hold", "latency_ms": 234,
        "request": {"text": "private transcript"}, "response": {"raw": "result"}})
    message = session.pc.send_app_message.call_args.args[0]
    assert message["type"] == "turn_decision" and message["decision"] == "hold"
    assert message["latency_ms"] == 234 and not session.events
    assert "request" not in message and "response" not in message
    server._record(session, {"kind": "handoff", "strategy": "JevTurnTaking",
        "turn": 1, "revision": 3, "reason": "max_silence_timeout", "text": "private transcript"})
    message = session.pc.send_app_message.call_args_list[-2].args[0]
    assert message["reason"] == "max_silence_timeout" and "text" not in message


@pytest.mark.asyncio
async def test_session_auth_isolation_limits_and_close():
    server = VoiceServer({"jev": object(), "silence": object()}, max_sessions=2)
    app = FastAPI()
    app.include_router(server.router, prefix="/voice")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as client:
            first = (await client.post("/voice/sessions", json={"agent": "jev", "debug": True})).json()
            second = (await client.post("/voice/sessions", json={"agent": "silence"})).json()
            assert first["session_id"] != second["session_id"]
            assert first["token"] != second["token"]
            assert (await client.post("/voice/sessions", json={"agent": "jev"})).status_code == 429
            path = f'/voice/sessions/{first["session_id"]}'
            wrong = {"Authorization": "Bearer " + second["token"]}
            right = {"Authorization": "Bearer " + first["token"]}
            assert (await client.get(path + "/events", headers=wrong)).status_code == 404
            assert (await client.get(path + "/events", headers=right)).json()["events"] == []
            assert (await client.delete(path)).status_code == 404
            assert (await client.delete(path, headers=right)).status_code == 200
            assert first["session_id"] not in server.sessions
            assert (await client.get(f'/voice/sessions/{second["session_id"]}/events', headers=wrong)).status_code == 403
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_auth_hook_and_bounded_debug_cursor():
    async def authenticate(request):
        if request.headers.get("authorization") != "Bearer application-test-token":
            raise HTTPException(401, "Unauthorized")

    server = VoiceServer({"jev": object()}, authenticate=authenticate)
    app = FastAPI()
    app.include_router(server.router)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as client:
            assert (await client.post("/sessions", json={"agent": "jev"})).status_code == 401
            response = await client.post("/sessions", json={"agent": "jev", "debug": True},
                headers={"Authorization": "Bearer application-test-token"})
            data = response.json()
            session = server.sessions[data["session_id"]]
            for index in range(505):
                server._record(session, {"kind": "test", "index": index})
            path = f'/sessions/{data["session_id"]}/events'
            headers = {"Authorization": "Bearer " + data["token"]}
            page = (await client.get(path, headers=headers)).json()
            assert page["truncated"] and page["cursor"] == 505
            assert len(page["events"]) == 500 and page["events"][0]["seq"] == 6
            tail = (await client.get(path + "?after=504", headers=headers)).json()
            assert not tail["truncated"] and len(tail["events"]) == 1
            assert (await client.get(path + "?after=-1", headers=headers)).status_code == 400
    finally:
        await server.close()
    assert not server.sessions and server.sweeper.done()


@pytest.mark.asyncio
async def test_local_origin_and_invalid_offer():
    server = VoiceServer({"jev": object()})
    app = FastAPI()
    app.include_router(server.router)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as client:
            assert (await client.post("/sessions", json={"agent": "jev"}, headers={"Origin": "https://evil.example"})).status_code == 403
            assert (await client.post("/sessions", json={"agent": "unknown"})).status_code == 404
            data = (await client.post("/sessions", json={"agent": "jev"})).json()
            response = await client.post(f'/sessions/{data["session_id"]}/offer',
                json={"type": "offer", "sdp": "bad sdp"}, headers={"Authorization": "Bearer " + data["token"]})
            assert response.status_code == 400
            assert not server.sessions
    finally:
        await server.close()
