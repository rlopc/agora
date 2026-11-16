import asyncio
import threading

import pytest
from fastapi.testclient import TestClient

from agora.core import Event, Participant, Room
from agora.store.sqlite import SqliteStore
from agora.web import WebChannel, create_app

PARTICIPANTS = [
    Participant("user", "User", "human"),
    Participant("claude", "Claude", "agent", "watch"),
    Participant("codex", "Codex", "agent", "codex_queue"),
]


class FakeWaker:
    def __init__(self):
        self.calls = []

    def wake(self, participant, pending):
        self.calls.append(participant.id)


@pytest.fixture
def setup():
    channel = WebChannel()
    codex = FakeWaker()
    room = Room(PARTICIPANTS, SqliteStore(":memory:"), [channel], {"codex": codex})
    return TestClient(create_app(room, channel)), room, codex


def test_page_and_static_files(setup):
    client, _, _ = setup
    assert "Agent room" in client.get("/").text
    assert client.get("/static/app.js").headers["content-type"].startswith("text/javascript")
    assert client.get("/static/app.css").status_code == 200
    assert client.get("/static/nothing.js").status_code == 404
    assert client.get("/static/index.html").status_code == 404
    assert client.get("/static/..%2Fapp.py").status_code == 404


def test_non_local_host_rejected(setup):
    client, _, _ = setup
    assert client.get("/api/state", headers={"host": "evil.example"}).status_code == 400


def test_participants(setup):
    client, _, _ = setup
    data = client.get("/api/participants").json()
    assert data["human"] == "user"
    assert [p["id"] for p in data["participants"]] == ["user", "claude", "codex"]


def test_send_as_human_by_default_and_wake(setup):
    client, _, codex = setup
    r = client.post("/api/messages", json={"text": "@codex hello @gemini"}).json()
    assert r["message"]["author"] == "user"
    assert r["notified"] == ["codex"]
    assert r["unknown"] == ["gemini"]
    assert codex.calls == ["codex"]
    assert [m["id"] for m in client.get("/api/messages").json()] == [1]


def test_room_errors_are_400(setup):
    client, _, _ = setup
    assert client.post("/api/messages", json={"text": "hello", "author": "nobody"}).status_code == 400
    assert client.post("/api/messages", json={"text": "hello", "reply_to": 7}).status_code == 400
    assert client.post("/api/messages", json={"text": ""}).status_code == 422
    assert client.get("/api/pending", params={"participant": "nobody"}).status_code == 400
    assert client.post("/api/wake/codex").status_code == 400


def test_pending_text_read_and_activity(setup):
    client, room, _ = setup
    client.post("/api/messages", json={"text": "@claude one"})
    client.post("/api/messages", json={"text": "@claude two", "author": "codex"})
    text = client.get("/api/pending", params={"participant": "claude", "format": "text"}).text
    assert text == "#1 user→claude: @claude one\n#2 codex→claude: @claude two"
    assert client.get("/api/pending", params={"participant": "claude", "since": 1}).json()[0]["id"] == 2

    client.get("/api/pending", params={"participant": "claude", "activity": True})
    assert room.store.wake_state("claude").last_activity is not None

    assert client.post("/api/read", json={"participant": "claude", "ids": [1, 2]}).json() == {"ok": True}
    assert client.get("/api/pending", params={"participant": "claude"}).json() == []


def test_pending_long_poll_returns_when_message_arrives(setup):
    client, room, _ = setup
    threading.Timer(0.3, lambda: room.send("user", "@claude now")).start()
    r = client.get("/api/pending", params={"participant": "claude", "wait": 5})
    assert [m["text"] for m in r.json()] == ["@claude now"]


def test_watching_marks_listening_during_the_wait(setup):
    client, room, _ = setup
    seen = []
    threading.Timer(0.3, lambda: seen.append(room.listening("claude"))).start()
    client.get("/api/pending", params={"participant": "claude", "wait": 0.6, "watching": True})
    assert seen == [True]
    assert room._listens["claude"] == 0


def test_pending_long_poll_expires_empty(setup):
    client, _, _ = setup
    assert client.get("/api/pending", params={"participant": "claude", "wait": 0.6}).json() == []


def test_pause_state_and_manual_wake(setup):
    client, _, codex = setup
    assert client.post("/api/pause", json={"paused": True}).json()["paused"] is True
    r = client.post("/api/messages", json={"text": "@codex while paused"}).json()
    assert r["notified"] == []
    state = client.get("/api/state").json()
    assert state["participants"]["codex"]["pending"] == 1
    assert client.post("/api/wake/codex").json() == {"ok": True}
    assert codex.calls == ["codex"]


def test_history_text(setup):
    client, _, _ = setup
    for i in range(3):
        client.post("/api/messages", json={"text": f"m{i}"})
    text = client.get("/api/history", params={"limit": 2, "format": "text"}).text
    assert text == "#2 user: m1\n#3 user: m2"


def test_close_ends_sse_streams():
    async def scenario():
        channel = WebChannel(heartbeat=10)
        stream = channel.sse_stream()
        await anext(stream)
        channel.close()
        with pytest.raises(StopAsyncIteration):
            await anext(stream)

    asyncio.run(scenario())


def test_long_poll_does_not_wait_if_channel_is_closed():
    channel = WebChannel()
    room = Room(PARTICIPANTS, SqliteStore(":memory:"), [channel])
    client = TestClient(create_app(room, channel))
    channel.close()
    import time

    start = time.monotonic()
    assert client.get("/api/pending", params={"participant": "claude", "wait": 5}).json() == []
    assert time.monotonic() - start < 2


def test_sse_channel_broadcasts_events():
    async def scenario():
        channel = WebChannel(heartbeat=0.05)
        stream = channel.sse_stream()
        assert await anext(stream) == ": connected\n\n"
        channel.publish(Event("message", {"id": 1, "text": "ñ"}))
        assert await anext(stream) == 'event: message\ndata: {"id": 1, "text": "ñ"}\n\n'
        assert await anext(stream) == ": heartbeat\n\n"
        await stream.aclose()
        assert channel._subscribers == set()

    asyncio.run(scenario())
