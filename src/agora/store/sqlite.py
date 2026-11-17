"""SQLite store for the agent room."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from pathlib import Path

from agora.core import Message, Round, WakeState

__all__ = ["SqliteStore", "archive"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    author TEXT NOT NULL,
    text TEXT NOT NULL,
    mentions TEXT NOT NULL,
    reply_to INTEGER REFERENCES messages(id),
    hops INTEGER NOT NULL,
    created TEXT NOT NULL,
    turns INTEGER NOT NULL DEFAULT 0,
    round INTEGER
);
CREATE TABLE IF NOT EXISTS mentions (
    message_id INTEGER NOT NULL REFERENCES messages(id),
    participant TEXT NOT NULL,
    PRIMARY KEY (participant, message_id)
);
CREATE TABLE IF NOT EXISTS reads (
    message_id INTEGER NOT NULL REFERENCES messages(id),
    participant TEXT NOT NULL,
    read_at TEXT NOT NULL,
    PRIMARY KEY (participant, message_id)
);
CREATE TABLE IF NOT EXISTS wakes (
    participant TEXT PRIMARY KEY,
    last_activity TEXT,
    last_wake TEXT,
    error TEXT
);
CREATE TABLE IF NOT EXISTS rounds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opener INTEGER NOT NULL REFERENCES messages(id),
    author TEXT NOT NULL,
    participants TEXT NOT NULL,
    opened TEXT NOT NULL,
    closed TEXT
);
CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Messages of an open round are visible only to their author; `:viewer` NULL sees everything.
_VISIBLE = """(
    :viewer IS NULL OR m.round IS NULL OR m.author = :viewer
    OR EXISTS (SELECT 1 FROM rounds r WHERE r.id = m.round AND r.closed IS NOT NULL)
)"""


def _date(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _message(row: sqlite3.Row) -> Message:
    return Message(
        id=row["id"],
        author=row["author"],
        text=row["text"],
        mentions=tuple(json.loads(row["mentions"])),
        reply_to=row["reply_to"],
        hops=row["hops"],
        created=datetime.fromisoformat(row["created"]),
        turns=row["turns"],
        round=row["round"],
    )


def _round(row: sqlite3.Row) -> Round:
    return Round(
        id=row["id"],
        opener=row["opener"],
        author=row["author"],
        participants=tuple(json.loads(row["participants"])),
        opened=datetime.fromisoformat(row["opened"]),
        closed=_date(row["closed"]),
    )


class SqliteStore:
    """A single shared connection guarded by a lock: the server is the only writer."""

    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=5000")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.executescript(_SCHEMA)
        # Databases created before the moderator turn limit or the blind rounds lack the columns.
        columns = {r["name"] for r in self._db.execute("PRAGMA table_info(messages)")}
        if "turns" not in columns:
            self._db.execute("ALTER TABLE messages ADD COLUMN turns INTEGER NOT NULL DEFAULT 0")
        if "round" not in columns:
            self._db.execute("ALTER TABLE messages ADD COLUMN round INTEGER")

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _rows(self, sql: str, params: Sequence[object] | Mapping[str, object] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, params).fetchall()

    def add_message(
        self,
        author: str,
        text: str,
        mentions: Sequence[str],
        reply_to: int | None,
        hops: int,
        created: datetime,
        turns: int = 0,
        round: int | None = None,
    ) -> Message:
        with self._lock:
            self._db.execute("BEGIN")
            try:
                cursor = self._db.execute(
                    "INSERT INTO messages (author, text, mentions, reply_to, hops, created, turns, round)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (author, text, json.dumps(list(mentions)), reply_to, hops, created.isoformat(), turns, round),
                )
                id = cursor.lastrowid
                assert id is not None  # noqa: S101 - narrows the type; always set after an INSERT
                self._db.executemany(
                    "INSERT INTO mentions (message_id, participant) VALUES (?, ?)",
                    [(id, m) for m in mentions],
                )
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        return Message(id, author, text, tuple(mentions), reply_to, hops, created, turns, round)

    def get_message(self, id: int) -> Message | None:
        rows = self._rows("SELECT * FROM messages WHERE id = ?", (id,))
        return _message(rows[0]) if rows else None

    def last_message(self) -> Message | None:
        rows = self._rows("SELECT * FROM messages ORDER BY id DESC LIMIT 1")
        return _message(rows[0]) if rows else None

    def messages(self, since: int = 0, limit: int = 200, viewer: str | None = None) -> list[Message]:
        rows = self._rows(
            f"SELECT * FROM messages m WHERE m.id > :since AND {_VISIBLE} ORDER BY m.id LIMIT :limit",  # noqa: S608 - constant clause
            {"since": since, "limit": limit, "viewer": viewer},
        )
        return [_message(r) for r in rows]

    def last_messages(self, limit: int, viewer: str | None = None) -> list[Message]:
        rows = self._rows(
            f"SELECT * FROM messages m WHERE {_VISIBLE} ORDER BY m.id DESC LIMIT :limit",  # noqa: S608 - constant clause
            {"limit": max(limit, 0), "viewer": viewer},
        )
        return [_message(r) for r in reversed(rows)]

    def pending(self, participant: str, since: int = 0, viewer: str | None = None) -> list[Message]:
        rows = self._rows(
            f"""
            SELECT m.* FROM messages m
            JOIN mentions c ON c.message_id = m.id AND c.participant = :participant
            LEFT JOIN reads r ON r.message_id = m.id AND r.participant = :participant
            WHERE m.id > :since AND m.author != :participant AND r.message_id IS NULL AND {_VISIBLE}
            ORDER BY m.id
            """,  # noqa: S608 - constant clause
            {"participant": participant, "since": since, "viewer": viewer},
        )
        return [_message(r) for r in rows]

    def open_round(self, author: str, opener: int, participants: Sequence[str], when: datetime) -> Round:
        with self._lock:
            cursor = self._db.execute(
                "INSERT INTO rounds (opener, author, participants, opened) VALUES (?, ?, ?, ?)",
                (opener, author, json.dumps(list(participants)), when.isoformat()),
            )
        id = cursor.lastrowid
        assert id is not None  # noqa: S101 - narrows the type; always set after an INSERT
        return Round(id, opener, author, tuple(participants), when)

    def current_round(self) -> Round | None:
        rows = self._rows("SELECT * FROM rounds WHERE closed IS NULL ORDER BY id DESC LIMIT 1")
        return _round(rows[0]) if rows else None

    def close_round(self, id: int, when: datetime) -> None:
        with self._lock:
            self._db.execute("UPDATE rounds SET closed = ? WHERE id = ? AND closed IS NULL", (when.isoformat(), id))

    def round_authors(self, id: int) -> set[str]:
        return {r["author"] for r in self._rows("SELECT DISTINCT author FROM messages WHERE round = ?", (id,))}

    def mark_read(self, participant: str, ids: Iterable[int], when: datetime) -> None:
        with self._lock:
            self._db.executemany(
                "INSERT OR IGNORE INTO reads (message_id, participant, read_at) VALUES (?, ?, ?)",
                [(i, participant, when.isoformat()) for i in ids],
            )

    def record_activity(self, participant: str, when: datetime) -> None:
        with self._lock:
            self._db.execute(
                """
                INSERT INTO wakes (participant, last_activity) VALUES (?, ?)
                ON CONFLICT (participant) DO UPDATE SET last_activity = excluded.last_activity
                """,
                (participant, when.isoformat()),
            )

    def record_wake(self, participant: str, when: datetime, error: str | None) -> None:
        with self._lock:
            self._db.execute(
                """
                INSERT INTO wakes (participant, last_wake, error) VALUES (?, ?, ?)
                ON CONFLICT (participant) DO UPDATE SET last_wake = excluded.last_wake, error = excluded.error
                """,
                (participant, when.isoformat(), error),
            )

    def wake_state(self, participant: str) -> WakeState:
        rows = self._rows("SELECT * FROM wakes WHERE participant = ?", (participant,))
        if not rows:
            return WakeState(participant)
        r = rows[0]
        return WakeState(participant, _date(r["last_activity"]), _date(r["last_wake"]), r["error"])

    def paused(self) -> bool:
        rows = self._rows("SELECT value FROM state WHERE key = 'paused'")
        return bool(rows) and json.loads(rows[0]["value"]) is True

    def set_paused(self, value: bool) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO state (key, value) VALUES ('paused', ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (json.dumps(value),),
            )


def archive(path: Path, folder: Path, when: datetime) -> Path | None:
    """Copies the database at `path` into `folder` and deletes it, WAL files included; None if there is none.

    `VACUUM INTO` writes a consistent copy that includes what is still only in the WAL file.
    """
    if not path.exists():
        return None
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{path.stem}-{when:%Y%m%d-%H%M%S}.db"
    db = sqlite3.connect(path)
    try:
        db.execute("VACUUM INTO ?", (str(target),))
    finally:
        db.close()
    for suffix in ("", "-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)
    return target
