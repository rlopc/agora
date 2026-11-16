from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from agora.core import Participant, Room, Store
from agora.store.sqlite import SqliteStore

_T0 = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


def check_store(factory: Callable[[], Store]) -> None:
    """Raises AssertionError if the store created by `factory` does not meet the contract."""
    s = factory()
    assert s.last_message() is None
    assert s.messages() == []
    assert s.paused() is False

    m1 = s.add_message("user", "hello @claude", ["claude"], None, 0, _T0)
    m2 = s.add_message("claude", "@codex @user look", ["codex", "user"], 1, 1, _T0 + timedelta(seconds=1))
    m3 = s.add_message("codex", "no mentions", [], None, 2, _T0 + timedelta(seconds=2))
    assert (m1.id, m2.id, m3.id) == (1, 2, 3)
    assert m2.mentions == ("codex", "user") and m2.reply_to == 1 and m2.hops == 1
    assert s.get_message(2) == m2
    assert s.get_message(99) is None
    assert s.last_message() == m3
    assert [m.id for m in s.messages()] == [1, 2, 3]
    assert [m.id for m in s.messages(since=1, limit=1)] == [2]
    assert [m.id for m in s.last_messages(2)] == [2, 3]
    assert m1.created == _T0

    assert [m.id for m in s.pending("claude")] == [1]
    assert [m.id for m in s.pending("codex")] == [2]
    assert [m.id for m in s.pending("codex", since=2)] == []
    s.mark_read("codex", [2], _T0)
    assert s.pending("codex") == []
    assert [m.id for m in s.pending("user")] == [2]
    s.mark_read("codex", [2], _T0)  # idempotent

    state = s.wake_state("codex")
    assert state.last_wake is None and state.last_activity is None and state.error is None
    s.record_wake("codex", _T0, None)
    s.record_activity("codex", _T0 + timedelta(seconds=5))
    s.record_wake("codex", _T0 + timedelta(seconds=9), "failed")
    state = s.wake_state("codex")
    assert state.last_wake == _T0 + timedelta(seconds=9)
    assert state.last_activity == _T0 + timedelta(seconds=5)
    assert state.error == "failed"
    s.record_wake("codex", _T0 + timedelta(seconds=10), None)
    assert s.wake_state("codex").error is None

    s.set_paused(True)
    assert s.paused() is True
    s.set_paused(False)
    assert s.paused() is False


def test_contract_in_memory():
    check_store(lambda: SqliteStore(":memory:"))


def test_contract_in_file(tmp_path):
    paths = iter(tmp_path / f"agora{i}.db" for i in range(10))
    check_store(lambda: SqliteStore(next(paths)))


def test_persists_across_openings(tmp_path):
    path = tmp_path / "data" / "agora.db"
    participants = [Participant("user", "User", "human"), Participant("codex", "Codex", "agent")]
    store = SqliteStore(path)
    Room(participants, store).send("user", "@codex hello")
    store.set_paused(True)
    store.close()

    reopened = SqliteStore(path)
    assert [m.text for m in reopened.pending("codex")] == ["@codex hello"]
    assert reopened.paused() is True
