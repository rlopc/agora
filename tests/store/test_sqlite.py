import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from agora.core import Participant, Room, Store
from agora.store.sqlite import SqliteStore, archive

_T0 = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


def check_store(factory: Callable[[], Store]) -> None:
    """Raises AssertionError if the store created by `factory` does not meet the contract."""
    s = factory()
    assert s.last_message() is None
    assert s.messages() == []
    assert s.paused() is False

    m1 = s.add_message("user", "hello @claude", ["claude"], None, 0, _T0)
    m2 = s.add_message("claude", "@codex @user look", ["codex", "user"], 1, 1, _T0 + timedelta(seconds=1))
    m3 = s.add_message("codex", "no mentions", [], None, 2, _T0 + timedelta(seconds=2), turns=1)
    assert (m1.id, m2.id, m3.id) == (1, 2, 3)
    assert (m1.turns, m3.turns) == (0, 1) and s.get_message(3) == m3
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


def test_contract_in_memory(open_store):
    check_store(lambda: open_store())


def test_contract_in_file(tmp_path, open_store):
    paths = iter(tmp_path / f"agora{i}.db" for i in range(10))
    check_store(lambda: open_store(next(paths)))


def test_adds_the_new_columns_to_an_older_database(tmp_path, open_store):
    path = tmp_path / "agora.db"
    old = sqlite3.connect(path)
    old.execute(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, author TEXT NOT NULL, text TEXT NOT NULL,"
        " mentions TEXT NOT NULL, reply_to INTEGER, hops INTEGER NOT NULL, created TEXT NOT NULL)"
    )
    old.execute(
        "INSERT INTO messages (author, text, mentions, hops, created) VALUES ('user', 'hi', '[]', 0, ?)",
        (_T0.isoformat(),),
    )
    old.commit()
    old.close()
    store = open_store(path)
    assert store.last_message().turns == 0
    assert store.add_message("moderator", "next", [], None, 0, _T0, turns=1).turns == 1
    assert store.last_message().round is None
    assert store.add_message("claude", "answer", [], None, 0, _T0, round=1).round == 1


def test_persists_across_openings(tmp_path, open_store):
    path = tmp_path / "data" / "agora.db"
    participants = [Participant("user", "User", "human"), Participant("codex", "Codex", "agent")]
    store = open_store(path)
    Room(participants, store).send("user", "@codex hello")
    store.set_paused(True)
    store.close()

    reopened = open_store(path)
    assert [m.text for m in reopened.pending("codex")] == ["@codex hello"]
    assert reopened.paused() is True


def test_archive_copies_the_database_and_deletes_it(tmp_path):
    db = tmp_path / "agora.db"
    store = SqliteStore(db)
    store.add_message("user", "hello", [], None, 0, _T0)
    store.close()
    target = archive(db, tmp_path / "archive", datetime(2026, 11, 17, 9, 5, 3))
    assert target == tmp_path / "archive" / "agora-20261117-090503.db"
    assert [p.name for p in tmp_path.iterdir()] == ["archive"]
    copy = SqliteStore(target)
    assert [m.text for m in copy.messages()] == ["hello"]
    copy.close()


def test_archive_without_database(tmp_path):
    assert archive(tmp_path / "agora.db", tmp_path / "archive", _T0) is None
    assert not (tmp_path / "archive").exists()


def test_open_round_hidden_from_other_viewers(open_store):
    s = open_store()
    s.add_message("user", "@claude @codex ideas?", ["claude", "codex"], None, 0, _T0)
    round = s.open_round("user", 1, ["claude", "codex"], _T0)
    assert s.current_round() == round
    s.add_message("claude", "mine @codex", ["user", "codex"], 1, 0, _T0, round=round.id)
    assert s.round_authors(round.id) == {"claude"}

    assert [m.id for m in s.messages()] == [1, 2]
    assert [m.id for m in s.messages(viewer="claude")] == [1, 2]
    assert [m.id for m in s.messages(viewer="codex")] == [1]
    assert [m.id for m in s.last_messages(5, viewer="codex")] == [1]
    assert [m.id for m in s.pending("codex", viewer="codex")] == [1]
    assert [m.id for m in s.pending("user")] == [2]

    s.close_round(round.id, _T0)
    assert s.current_round() is None
    assert [m.id for m in s.messages(viewer="codex")] == [1, 2]
    assert [m.id for m in s.pending("codex", viewer="codex")] == [1, 2]
