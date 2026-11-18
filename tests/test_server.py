"""Real server in a thread, CLIs and MCP as separate processes, and a fake Codex."""

import asyncio
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from agora.agents.tool import UnconfiguredWaker
from agora.server import build, load_config

SCRIPTS = Path(sys.executable).parent
TOKEN = "test-token"


def executable(name):
    return str(SCRIPTS / name)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(tmp_path):
    log = tmp_path / "codex_args.jsonl"
    fake_codex = tmp_path / "fake_codex.py"
    fake_codex.write_text(
        f"import json, sys\nopen({str(log)!r}, 'a', encoding='utf-8').write(json.dumps(sys.argv[1:]) + '\\n')\n",
        encoding="utf-8",
    )
    port = free_port()
    config = {
        "room": {"port": port, "db": "agora.db"},
        "participants": [
            {"id": "user", "name": "User", "type": "human"},
            {"id": "claude", "name": "Claude", "type": "agent", "tool": "claude", "waker": "watch"},
            {
                "id": "codex",
                "name": "Codex",
                "type": "agent",
                "tool": "codex",
                "codex_thread": "test-thread",
                "executable": [sys.executable, str(fake_codex)],
            },
        ],
    }
    s = build(config, tmp_path, env={"AGORA_TOKEN": TOKEN})
    uv = uvicorn.Server(uvicorn.Config(s.app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=uv.run, daemon=True)
    thread.start()
    while not uv.started:
        time.sleep(0.05)
    url = f"http://127.0.0.1:{port}"
    yield url, log
    uv.should_exit = True
    thread.join(5)
    s.close()


def wait_for(condition, timeout=10):
    limit = time.monotonic() + timeout
    while time.monotonic() < limit:
        if condition():
            return True
        time.sleep(0.1)
    return False


def test_watch_wakes_claude(server):
    url, _ = server
    process = subprocess.Popen(
        [executable("agora-watch"), "--participant", "claude", "--url", url, "--wait", "1"],
        stdout=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        httpx.post(f"{url}/api/messages", json={"text": "@claude hello → ñ"})
        line = process.stdout.readline().strip()
        assert line == "#1 user→claude: @claude hello → ñ"

        # Without activity from Claude, the second wake is batched: it is not printed yet.
        httpx.post(f"{url}/api/messages", json={"text": "@claude second"})
        # Claude replies to #1 (activity): it is woken again for #2, which it had not seen.
        httpx.post(f"{url}/api/messages", json={"text": "@user seen", "author": "claude", "reply_to": 1})
        assert process.stdout.readline().strip() == "#2 user→claude: @claude second"
        # Claude handles the wake (activity) and Codex writes to it: hop 2, it is woken and #4 is printed.
        httpx.post(f"{url}/api/read", json={"participant": "claude", "ids": [2]})
        httpx.post(f"{url}/api/messages", json={"text": "@claude what about this?", "author": "codex"})
        assert process.stdout.readline().strip() == "#4 codex→claude: @claude what about this?"

        # Hop limit: #6 (hop 4) is stored but records no wake; it arrives with the next one (#7).
        httpx.post(f"{url}/api/read", json={"participant": "claude", "ids": [2, 4]})
        httpx.post(f"{url}/api/messages", json={"text": "@codex ok", "author": "claude"})
        wake = httpx.get(f"{url}/api/state").json()["participants"]["claude"]["last_wake"]
        r = httpx.post(f"{url}/api/messages", json={"text": "@claude third hop", "author": "codex"}).json()
        assert r["message"]["hops"] == 4 and r["notified"] == []
        assert httpx.get(f"{url}/api/state").json()["participants"]["claude"]["last_wake"] == wake
        httpx.post(f"{url}/api/messages", json={"text": "@claude you decide"})
        assert process.stdout.readline().strip() == "#6 codex→claude: @claude third hop"
        assert process.stdout.readline().strip() == "#7 user→claude: @claude you decide"
    finally:
        process.terminate()
        process.wait()
        process.stdout.close()


def stop_hook(url, in_sh=False):
    command = [executable("agora-watch"), "--participant", "claude", "--url", url, "--wait", "1", "--stop-hook"]
    if in_sh:  # `; true`: sh does not exec, so the hook has a parent to lose
        command = ["/bin/sh", "-c", " ".join(command) + "; true"]
    return subprocess.Popen(command, stdout=subprocess.PIPE, text=True, encoding="utf-8")


def listening(url):
    return httpx.get(f"{url}/api/state").json()["participants"]["claude"]["listening"]


def test_stop_hook_continues_on_the_next_wake(server):
    url, _ = server
    httpx.post(f"{url}/api/messages", json={"text": "@claude seen and left pending"})
    httpx.get(f"{url}/api/pending", params={"participant": "claude", "activity": True})
    hook = stop_hook(url)
    try:
        # Woken before its last activity: it already saw it, so it does not loop on it.
        assert wait_for(lambda: listening(url))
        assert hook.poll() is None
        httpx.post(f"{url}/api/messages", json={"text": "@claude new"})
        out, _ = hook.communicate(timeout=10)
    finally:
        hook.kill()
    # The messages arrive through the PreInvocation hook, before the model call that follows.
    assert json.loads(out) == {
        "decision": "continue",
        "reason": "[agora] The room woke you: its pending messages follow.",
    }


def test_stop_hook_continues_at_once_for_a_wake_it_did_not_see(server):
    url, _ = server
    httpx.post(f"{url}/api/messages", json={"text": "@claude during its last model call"})
    out, _ = stop_hook(url).communicate(timeout=10)
    assert json.loads(out)["decision"] == "continue"


def test_stop_hook_stops_waiting_when_its_parent_dies(server):
    url, _ = server
    parent = stop_hook(url, in_sh=True)
    assert wait_for(lambda: listening(url))
    [hook] = subprocess.run(["/usr/bin/pgrep", "-P", str(parent.pid)], capture_output=True, text=True).stdout.split()
    parent.kill()
    parent.wait(timeout=5)
    parent.stdout.close()
    assert wait_for(lambda: not alive(int(hook)))


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_stop_hook_with_the_room_down():
    out, _ = stop_hook("http://127.0.0.1:9").communicate(timeout=10)
    assert json.loads(out) == {}


def test_pending_for_hooks(server):
    url, _ = server
    pending = [executable("agora-pending"), "--participant", "claude", "--url", url]
    empty = subprocess.run(pending, capture_output=True, text=True, encoding="utf-8")
    assert empty.returncode == 0 and empty.stdout == ""

    httpx.post(f"{url}/api/messages", json={"text": "@claude review"})
    full = subprocess.run(pending, capture_output=True, text=True, encoding="utf-8")
    assert full.stdout.splitlines() == [
        "[agora] 1 pending message(s) for @claude. Answer in the room with the `send` tool of the `agora` MCP "
        "(set `reply_to`), not in this chat; use `mark_read` for what needs no reply:",
        "#1 user→claude: @claude review",
    ]

    as_json = subprocess.run(
        [*pending, "--json-event", "UserPromptSubmit"], capture_output=True, text=True, encoding="utf-8"
    )
    output = json.loads(as_json.stdout)["hookSpecificOutput"]
    assert output["hookEventName"] == "UserPromptSubmit"
    assert output["additionalContext"].endswith("#1 user→claude: @claude review")

    down = subprocess.run(
        [executable("agora-pending"), "--participant", "claude", "--url", "http://127.0.0.1:9"],
        capture_output=True,
        text=True,
    )
    assert down.returncode == 0 and down.stdout == ""


def test_mcp_over_stdio_and_wake_to_codex(server):
    url, log = server

    async def as_codex():
        params = StdioServerParameters(
            command=executable("agora-mcp"), args=[], env={"AGORA_PARTICIPANT": "codex", "AGORA_URL": url}
        )
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            r = await session.call_tool("pending", {})
            pending = r.content[0].text
            r = await session.call_tool("send", {"text": "@user done", "reply_to": 1})
            return pending, r.content[0].text

    httpx.post(f"{url}/api/messages", json={"text": "@codex review the schema & the links"})
    assert wait_for(lambda: log.exists())
    args = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert args[:3] == ["queue", "--thread", "test-thread"]
    assert "#1 user→codex: @codex review the schema & the links" in args[4]

    pending, sent = asyncio.run(as_codex())
    assert pending == "#1 user→codex: @codex review the schema & the links"
    assert sent == "#2"
    assert httpx.get(f"{url}/api/state").json()["participants"]["codex"]["pending"] == 0


def test_codex_without_thread_is_reported(tmp_path):
    config = {
        "room": {"db": "agora.db"},
        "participants": [
            {"id": "user", "name": "User", "type": "human"},
            {"id": "codex", "name": "Codex", "type": "agent", "tool": "codex", "codex_thread": ""},
        ],
    }
    s = build(config, tmp_path, env={})
    assert isinstance(s.room.wakers["codex"], UnconfiguredWaker)
    s.room.send("user", "@codex hello")
    assert wait_for(lambda: s.room.state()["participants"]["codex"]["error"] is not None)
    assert "codex_thread" in s.room.state()["participants"]["codex"]["error"]
    s.close()

    with_variable = build(config, tmp_path, env={"AGORA_CODEX_THREAD": "abc"})
    assert with_variable.room.wakers["codex"].thread == "abc"
    with_variable.close()


def test_codex_thread_is_reread_from_agora_toml(tmp_path):
    path = tmp_path / "agora.toml"
    template = """
[[participants]]
id = "user"
name = "U"
type = "human"

[[participants]]
id = "codex"
name = "C"
type = "agent"
tool = "codex"
codex_thread = "{}"
"""
    path.write_text(template.format(""), encoding="utf-8")
    s = build(load_config(path), tmp_path, env={}, config_path=path)
    waker = s.room.wakers["codex"]
    assert waker.current_thread() == ""
    path.write_text(template.format("new-session"), encoding="utf-8")
    assert waker.current_thread() == "new-session"
    s.close()


def test_build_reads_the_role(tmp_path):
    config = {
        "room": {"db": str(tmp_path / "s.db")},
        "participants": [
            {"id": "user", "name": "User", "type": "human"},
            {"id": "moderator", "name": "Moderator", "type": "agent", "tool": "claude", "role": "moderator"},
        ],
    }
    s = build(config, tmp_path, env={})
    try:
        p = s.room.participants["moderator"]
        assert p.role == "moderator"
        assert "role" not in p.options
    finally:
        s.close()


def test_build_generates_a_token_unless_given(tmp_path):
    config = {"room": {"db": str(tmp_path / "s.db")}, "participants": [{"id": "user", "name": "U", "type": "human"}]}
    servers = [
        build(config, tmp_path, env={}),
        build(config, tmp_path, env={}),
        build(config, tmp_path, env={"AGORA_TOKEN": "x"}),
    ]
    try:
        first, second, given = (s.token for s in servers)
        assert len(first) >= 32 and first != second and given == "x"
    finally:
        for s in servers:
            s.close()


def test_main_with_invalid_role_exits_with_message(tmp_path, monkeypatch):
    from agora.server import main

    # Without the validation, main would start uvicorn and the test would hang.
    monkeypatch.setattr(
        "uvicorn.Server.run", lambda self, *a, **k: pytest.fail("main should not have started the server")
    )

    config = tmp_path / "agora.toml"
    config.write_text(
        "[room]\nport = 8799\n\n"
        '[[participants]]\nid = "user"\nname = "User"\ntype = "human"\n\n'
        '[[participants]]\nid = "moderator"\nname = "M"\ntype = "agent"\ntool = "claude"\nrole = "moderators"\n',
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="invalid role for moderator"):
        main(["--config", str(config)])


NEW = """
[room]
db = "data/agora.db"

[tools.claude]
model = "opus"

[tools.codex]
model = "gpt-5.6-terra"
homes = '{homes}'

[[participants]]
id = "user"
name = "User"
type = "human"

[[participants]]
id = "moderator"
name = "Moderator"
type = "agent"
role = "moderator"
folder = "."
tool = "{tool}"
{extra}
"""


def new_config(tmp_path, extra="", tool="claude"):
    path = tmp_path / "agora.toml"
    path.write_text(NEW.format(homes=tmp_path / "homes", extra=extra, tool=tool), encoding="utf-8")
    return path


def test_build_codex_derives_its_waker(tmp_path):
    from agora.tools.codex.waker import CodexQueueWaker

    path = new_config(tmp_path, tool="codex")
    s = build(load_config(path), tmp_path, env={}, config_path=path)
    try:
        w = s.room.wakers["moderator"]
        assert isinstance(w, CodexQueueWaker)
        assert w.codex_home == str(tmp_path / "homes" / "moderator")
        assert s.room.participants["moderator"].role == "moderator"
    finally:
        s.close()


def test_build_claude_uses_the_concierge(tmp_path):
    from agora.tools.claude.waker import ConciergeWaker

    path = new_config(tmp_path)
    s = build(load_config(path), tmp_path, env={}, config_path=path)
    try:
        w = s.room.wakers["moderator"]
        assert isinstance(w, ConciergeWaker) and w.cwd == str(tmp_path) and w.model == "haiku"
    finally:
        s.close()


def test_concierge_model(tmp_path):
    path = new_config(tmp_path, extra='concierge_model = "sonnet"')
    s = build(load_config(path), tmp_path, env={}, config_path=path)
    try:
        assert s.room.wakers["moderator"].model == "sonnet"
    finally:
        s.close()


def test_codex_without_thread_with_folder_finds_its_session(tmp_path):
    from agora.tools.codex.waker import CodexQueueWaker

    path = new_config(tmp_path, tool="codex")
    s = build(load_config(path), tmp_path, env={})
    try:
        w = s.room.wakers["moderator"]
        assert isinstance(w, CodexQueueWaker) and w.cwd == str(tmp_path)
        assert w.current_thread() == ""
    finally:
        s.close()


REQUEST = {"session_id": "s1", "tool_name": "Bash", "tool_input": {"command": "rm -rf build", "description": "Clean"}}


def ask_permission(url, *extra, request=REQUEST):
    hook = subprocess.Popen(
        [executable("agora-permission"), "--participant", "claude", "--url", url, *extra],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    assert hook.stdin is not None
    hook.stdin.write(json.dumps(request))
    hook.stdin.close()
    return hook


def permissions(url):
    return httpx.get(f"{url}/api/permissions").json()["permissions"]


def test_permission_answered_in_the_room(server):
    url, _ = server
    hook = ask_permission(url)
    assert wait_for(lambda: permissions(url))
    [item] = permissions(url)
    assert item["summary"] == "Bash: rm -rf build" and item["status"] == "pending"
    port = url.rsplit(":", 1)[1]
    r = httpx.post(f"{url}/api/permissions/{item['id']}", json={"allow": True}, cookies={f"agora-{port}": TOKEN})
    assert r.json()["status"] == "allowed"
    out, _ = hook.communicate(timeout=10)
    assert hook.returncode == 0
    assert json.loads(out) == {
        "hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": {"behavior": "allow"}}
    }
    assert httpx.get(f"{url}/api/messages").json() == []  # not a message: it is no hop


def test_antigravity_permission_denied_in_the_room(server):
    url, _ = server
    request = {"conversationId": "c1", "toolCall": {"name": "run_command", "args": {"CommandLine": "rm -rf build"}}}
    hook = ask_permission(url, "--antigravity", request=request)
    assert wait_for(lambda: permissions(url))
    [item] = permissions(url)
    assert item["summary"] == "run_command: rm -rf build"
    port = url.rsplit(":", 1)[1]
    httpx.post(f"{url}/api/permissions/{item['id']}", json={"allow": False}, cookies={f"agora-{port}": TOKEN})
    out, _ = hook.communicate(timeout=10)
    assert hook.returncode == 0
    assert json.loads(out) == {"decision": "deny", "reason": "Denied by the user in the room."}


def test_permission_allowed_in_the_terminal(server):
    """Allowing in the terminal does not stop the hook: the hook after the call closes the request."""
    url, _ = server
    hook = ask_permission(url)
    assert wait_for(lambda: permissions(url))
    done = ask_permission(url, "--done")
    assert done.communicate(timeout=10)[0] == "" and done.returncode == 0
    out, _ = hook.communicate(timeout=10)
    assert hook.returncode == 0 and out == ""
    assert permissions(url)[0]["status"] == "terminal"


def test_permission_denied_in_the_terminal(server):
    """Denying in the terminal kills the hook: the room sees the connection close."""
    url, _ = server
    hook = ask_permission(url)
    assert wait_for(lambda: permissions(url))
    hook.kill()
    hook.communicate(timeout=5)
    assert wait_for(lambda: permissions(url)[0]["status"] == "terminal")


def test_permission_stops_waiting_when_its_parent_dies(server):
    """agy kills only the process it started (`uv run`): the hook must not keep waiting."""
    url, _ = server
    command = f"{executable('agora-permission')} --participant claude --url {url}; true"  # `; true`: sh does not exec
    parent = subprocess.Popen(["/bin/sh", "-c", command], stdin=subprocess.PIPE, text=True)
    assert parent.stdin is not None
    parent.stdin.write(json.dumps(REQUEST))
    parent.stdin.close()
    assert wait_for(lambda: permissions(url))
    parent.kill()
    parent.wait(timeout=5)
    assert wait_for(lambda: permissions(url)[0]["status"] == "terminal")


def test_permission_with_the_room_down():
    hook = ask_permission("http://127.0.0.1:9")
    out, _ = hook.communicate(timeout=10)
    assert hook.returncode == 0 and out == ""
