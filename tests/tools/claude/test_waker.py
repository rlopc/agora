import json
import os
import subprocess
import sys
from datetime import UTC, datetime

import pytest

from agora.core import Message, Participant, WakeError
from agora.tools.claude.waker import ConciergeWaker, find_claude_session

CLAUDE = Participant("claude", "Claude", "agent", "concierge")
T = datetime(2026, 10, 29, tzinfo=UTC)
PENDING = [Message(5, "user", "@claude hello & goodbye", ("claude",), None, 0, T)]


def session(directory, name, cwd, pid=None, kind="interactive", updated=1):
    data = {"pid": pid or os.getpid(), "cwd": cwd, "kind": kind, "name": name, "updatedAt": updated}
    (directory / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def sessions(tmp_path):
    d = tmp_path / "sessions"
    d.mkdir()
    return d


@pytest.fixture
def fake_claude(tmp_path):
    """Records arguments and input; answers with the JSON given in FAKE_CLAUDE_RESULT."""
    record = tmp_path / "call.json"
    script = tmp_path / "fake_claude.py"
    script.write_text(
        "import json, os, sys\n"
        f"open({str(record)!r}, 'w', encoding='utf-8').write(json.dumps({{'args': sys.argv[1:], 'stdin': sys.stdin.buffer.read().decode('utf-8')}}))\n"
        "print(os.environ.get('FAKE_CLAUDE_RESULT', json.dumps({'result': 'sent', 'is_error': False})))\n",
        encoding="utf-8",
    )
    return [sys.executable, str(script)], record


def test_finds_the_most_recent_live_interactive_session(sessions):
    session(sessions, "kb-old", r"/KnowledgeBase", updated=1)
    session(sessions, "kb-new", "/KnowledgeBase/", updated=5)
    session(sessions, "kb-dead", r"/KnowledgeBase", pid=999_999_9, updated=9)
    session(sessions, "kb-background", r"/KnowledgeBase", kind="background", updated=9)
    session(sessions, "other", r"/Dev", updated=9)
    (sessions / "broken.json").write_text("{", encoding="utf-8")
    assert find_claude_session(r"/KnowledgeBase", sessions) == "kb-new"
    assert find_claude_session(r"/Nothing", sessions) is None


def test_hands_the_notice_over_stdin(sessions, fake_claude):
    executable, record = fake_claude
    session(sessions, "knowledgebase-a1", r"/KnowledgeBase")
    ConciergeWaker(r"/KnowledgeBase", executable, sessions_dir=sessions).wake(CLAUDE, PENDING)
    call = json.loads(record.read_text(encoding="utf-8"))
    assert call["args"][:3] == ["-p", "--model", "haiku"]
    assert "--strict-mcp-config" in call["args"]
    assert call["args"][call["args"].index("--allowedTools") + 1] == "ToolSearch,ListAgents,SendMessage"
    assert "«knowledgebase-a1»" in call["stdin"]
    assert "#5 user→claude: @claude hello & goodbye" in call["stdin"]


def test_does_nothing_if_the_session_is_listening(sessions, fake_claude):
    executable, record = fake_claude
    session(sessions, "knowledgebase-a1", r"/KnowledgeBase")
    concierge = ConciergeWaker(r"/KnowledgeBase", executable, sessions_dir=sessions, listening=lambda: True)
    concierge.wake(CLAUDE, PENDING)
    assert not record.exists()


def test_without_open_session(sessions, fake_claude):
    executable, _ = fake_claude
    with pytest.raises(WakeError, match=r"there is no Claude Code session.*waiting for a dialog"):
        ConciergeWaker(r"/KnowledgeBase", executable, sessions_dir=sessions).wake(CLAUDE, PENDING)


@pytest.mark.parametrize(
    "output, error",
    [
        (json.dumps({"result": "SendMessage not available", "is_error": False}), "could not notify"),
        (json.dumps({"result": "sent", "is_error": True}), "could not notify"),
        ("Error: not logged in", "not logged in"),
    ],
)
def test_concierge_failures(sessions, fake_claude, monkeypatch, output, error):
    executable, _ = fake_claude
    session(sessions, "knowledgebase-a1", r"/KnowledgeBase")
    monkeypatch.setenv("FAKE_CLAUDE_RESULT", output)
    with pytest.raises(WakeError, match=error):
        ConciergeWaker(r"/KnowledgeBase", executable, sessions_dir=sessions).wake(CLAUDE, PENDING)


def test_concierge_does_not_inherit_claude_variables(sessions, fake_claude, monkeypatch):
    executable, _ = fake_claude
    session(sessions, "knowledgebase-a1", r"/KnowledgeBase")
    seen = {}
    real = subprocess.run

    def spy(*args, **kwargs):
        seen.update(kwargs.get("env") or {})
        return real(*args, **kwargs)

    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
    monkeypatch.setattr("agora.tools.claude.waker.subprocess.run", spy)
    ConciergeWaker(r"/KnowledgeBase", executable, sessions_dir=sessions).wake(CLAUDE, PENDING)
    assert "PATH" in {k.upper() for k in seen}
    assert not [k for k in seen if k.upper().startswith("CLAUDE")]
