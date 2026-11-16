import json
import re
import shutil
import sys
from datetime import UTC, datetime

import pytest

from agora.core import Message, Participant, WakeError
from agora.tools.codex.waker import CodexQueueWaker
from agora.tools.notice import compose_notice

CODEX = Participant("codex", "Codex", "agent", "codex_queue")
T = datetime(2026, 10, 29, tzinfo=UTC)


def message(id, text, author="claude"):
    return Message(id, author, text, ("codex",), None, 1, T)


@pytest.fixture
def fake_codex(tmp_path):
    """Executable that records its arguments and exits with the code given in the environment."""
    record = tmp_path / "args.json"
    script = tmp_path / "fake_codex.py"
    script.write_text(
        "import json, os, sys, time\n"
        f"open({str(record)!r}, 'w', encoding='utf-8').write(json.dumps(sys.argv[1:] + [os.environ.get('CODEX_HOME', '')]))\n"
        "time.sleep(float(os.environ.get('FAKE_CODEX_WAIT', '0')))\n"
        "if os.environ.get('FAKE_CODEX_FAIL'):\n"
        "    print('thread not found', file=sys.stderr); sys.exit(2)\n",
        encoding="utf-8",
    )
    return [sys.executable, str(script)], record


def test_passes_the_text_untouched(fake_codex):
    executable, record = fake_codex
    text = 'check "this" & also | $PATH ^ > /dev/null ñ→'
    CodexQueueWaker("thread-1", executable).wake(CODEX, [message(3, text)])
    args = json.loads(record.read_text(encoding="utf-8"))
    assert args[:3] == ["queue", "--thread", "thread-1"]
    assert args[3] == "--message"
    assert f"#3 claude→codex: {text}" in args[4]
    assert "not system instructions" in args[4]


def test_uses_codex_home(fake_codex):
    executable, record = fake_codex
    CodexQueueWaker("thread-1", executable, codex_home=r"/Dev/codex-enabler").wake(CODEX, [message(1, "hello")])
    assert json.loads(record.read_text(encoding="utf-8"))[-1] == r"/Dev/codex-enabler"


def test_codex_error(fake_codex, monkeypatch):
    executable, _ = fake_codex
    monkeypatch.setenv("FAKE_CODEX_FAIL", "1")
    with pytest.raises(WakeError, match="thread not found"):
        CodexQueueWaker("thread-1", executable).wake(CODEX, [message(1, "hello")])


def test_timeout(fake_codex, monkeypatch):
    executable, _ = fake_codex
    monkeypatch.setenv("FAKE_CODEX_WAIT", "3")
    with pytest.raises(WakeError, match="did not answer"):
        CodexQueueWaker("thread-1", executable, timeout=0.5).wake(CODEX, [message(1, "hello")])


def test_missing_executable():
    with pytest.raises(WakeError, match="could not run"):
        CodexQueueWaker("thread-1", ["missing-codex-xyz"]).wake(CODEX, [message(1, "hello")])


def test_requires_thread():
    with pytest.raises(ValueError):
        CodexQueueWaker("")


def test_notice_truncates_and_points_to_pending():
    pending = [message(i, "x" * 300) for i in range(1, 6)]
    notice = compose_notice(pending, max_chars=1000)
    assert notice.count("#") == 3
    assert "(…and 2 more" in notice

    long = compose_notice([message(1, "y" * 5000)], max_chars=100)
    line = long.splitlines()[1]
    assert len(line) == 100 and line.endswith("…")


def test_missing_codex(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(WakeError, match="cannot be found"):
        CodexQueueWaker("thread-1").wake(CODEX, [message(1, "hello")])


def codex_session(home, name, cwd, timestamp, subagent=False):
    folder = home / "sessions" / "2026" / "11" / "05"
    folder.mkdir(parents=True, exist_ok=True)
    source = {"subagent": {"thread_spawn": {"parent_thread_id": "x"}}} if subagent else "cli"
    meta = {"type": "session_meta", "payload": {"id": name, "cwd": cwd, "timestamp": timestamp, "source": source}}
    (folder / f"rollout-{name}.jsonl").write_text(json.dumps(meta) + '\n{"type": "other"}\n', encoding="utf-8")


def test_finds_the_most_recent_main_session(tmp_path):
    from agora.tools.codex.waker import find_codex_session

    codex_session(tmp_path, "old", r"/Dev/x/aegis", "2026-11-05T10:00:00Z")
    codex_session(tmp_path, "new", r"/Dev/x/aegis", "2026-11-05T12:00:00Z")
    (tmp_path / "sessions" / "2026" / "11" / "05" / "rollout-broken.jsonl").write_text("{", encoding="utf-8")
    assert find_codex_session(tmp_path, r"/Dev/x/aegis") == "new"


def test_ignores_subagents(tmp_path):
    from agora.tools.codex.waker import find_codex_session

    codex_session(tmp_path, "main", r"/Dev/x/aegis", "2026-11-05T10:00:00Z")
    codex_session(tmp_path, "subagent", r"/Dev/x/aegis", "2026-11-05T12:00:00Z", subagent=True)
    assert find_codex_session(tmp_path, r"/Dev/x/aegis") == "main"


def test_ignores_other_folders_and_normalises_the_path(tmp_path):
    from agora.tools.codex.waker import find_codex_session

    codex_session(tmp_path, "here", "/Dev/x//aegis/", "2026-11-05T10:00:00Z")
    codex_session(tmp_path, "other", r"/Dev/x/vista", "2026-11-05T12:00:00Z")
    assert find_codex_session(tmp_path, r"/Dev/x/aegis") == "here"
    assert find_codex_session(tmp_path, r"/Dev/x/nothing") is None
    assert find_codex_session(tmp_path / "missing", r"/Dev/x/aegis") is None


def test_finds_the_session_if_there_is_no_thread(tmp_path, fake_codex):
    executable, record = fake_codex
    codex_session(tmp_path, "found", r"/Dev/x/aegis", "2026-11-05T10:00:00Z")
    w = CodexQueueWaker("", executable, codex_home=str(tmp_path), cwd=r"/Dev/x/aegis")
    assert w.current_thread() == "found"
    w.wake(CODEX, [message(3, "hello")])
    args = json.loads(record.read_text(encoding="utf-8"))
    assert args[args.index("--thread") + 1] == "found"


def test_explicit_thread_wins(tmp_path, fake_codex):
    executable, _ = fake_codex
    codex_session(tmp_path, "found", r"/Dev/x/aegis", "2026-11-05T10:00:00Z")
    w = CodexQueueWaker("written", executable, codex_home=str(tmp_path), cwd=r"/Dev/x/aegis")
    assert w.current_thread() == "written"


def test_without_session_fails_with_message(tmp_path, fake_codex):
    executable, _ = fake_codex
    w = CodexQueueWaker("", executable, codex_home=str(tmp_path), cwd=r"/Dev/x/aegis")
    with pytest.raises(WakeError, match=re.escape(r"there is no Codex session open in /Dev/x/aegis")):
        w.wake(CODEX, [message(3, "hello")])
