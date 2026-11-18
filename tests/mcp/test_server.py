import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient
from mcp.server.mcpserver.exceptions import ToolError

from agora.core import Participant, Room
from agora.mcp import create_server
from agora.web import WebChannel, create_app

PARTICIPANTS = [
    Participant("user", "User", "human"),
    Participant("claude", "Claude", "agent", "watch"),
    Participant("codex", "Codex", "agent", "codex_queue"),
]


class Waker:
    def __init__(self):
        self.wakes = 0

    def wake(self, participant, pending):
        self.wakes += 1


def text(result):
    return "\n".join(c.text for c in result.content)


def call(mcp, tool, **arguments):
    return asyncio.run(mcp.call_tool(tool, arguments))


@pytest.fixture
def setup(open_store):
    channel = WebChannel()
    codex = Waker()
    room = Room(PARTICIPANTS, open_store(), [channel], {"codex": codex, "claude": Waker()})
    client = TestClient(create_app(room, channel))
    return create_server(client, "claude"), room, codex


def test_exposed_tools(setup):
    mcp, _, _ = setup
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert names == {"pending", "send", "mark_read", "history", "close_round"}


def test_full_conversation(setup):
    mcp, room, codex = setup
    room.send("user", "@claude review phase 1")
    assert text(call(mcp, "pending")) == "#1 user→claude: @claude review phase 1"

    r = text(call(mcp, "send", text="@codex thoughts? @gemini", reply_to=1))
    assert r == "#2 · notified: @codex · unknown mentions: @gemini"
    assert codex.wakes == 1
    assert text(call(mcp, "pending")) == "No pending messages."

    room.send("codex", "@claude minor note")
    assert text(call(mcp, "mark_read", ids=[3])) == "ok"
    assert room.pending("claude") == []

    assert text(call(mcp, "history", limit=2)).splitlines()[0].startswith("#2 claude→codex (re #1)")
    assert text(call(mcp, "history", since=2)).startswith("#3 codex→claude")


def test_pending_records_activity(setup):
    mcp, room, _ = setup
    call(mcp, "pending")
    assert room.store.wake_state("claude").last_activity is not None


def test_readable_errors(setup):
    mcp, _, _ = setup
    with pytest.raises(ToolError, match="message #99"):
        call(mcp, "send", text="hello", reply_to=99)

    down = create_server(httpx.Client(base_url="http://127.0.0.1:9", timeout=1), "claude")
    with pytest.raises(ToolError, match="room not available"):
        call(down, "pending")


def test_blind_round(setup):
    mcp, room, _ = setup
    room.send("user", "@claude @codex which database?", blind=True)
    room.send("codex", "Postgres", reply_to=1)
    assert text(call(mcp, "history")) == "#1 user→claude,codex: @claude @codex which database?"
    assert text(call(mcp, "send", text="SQLite, unlike @codex", reply_to=1)) == (
        "#3 · held until the blind round closes: @codex"
    )
    assert len(text(call(mcp, "history")).splitlines()) == 3
    with pytest.raises(ToolError, match="no open blind round"):
        call(mcp, "close_round")
