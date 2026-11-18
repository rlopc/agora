"""Permission requests that an agent's hook waits on and the user can answer from the room.

They live in memory only: a request exists while its hook waits, so after a restart there is
nothing left to answer. Claude Code's own prompt stays open meanwhile, so whichever answers
first, the room or the terminal, wins; Codex asks in its terminal only if the room does not decide.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Literal

from agora.core.model import Event, Participant
from agora.core.ports import Channel
from agora.core.room import RoomError

# Seconds the hook waits for an answer from the room: the default hook timeout in Claude Code and Codex.
PERMISSION_WAIT = 600
# Answered requests kept for the UI, besides the pending ones.
KEEP = 20

# `terminal`: the hook stopped waiting (answered in the terminal, timed out or the room stopped).
Status = Literal["pending", "allowed", "denied", "terminal"]


class PermissionNotFound(LookupError):
    pass


class PermissionAnswered(RoomError):
    """The request is no longer pending."""


@dataclass(frozen=True)
class Permission:
    id: int
    participant: str
    summary: str
    created: datetime
    status: Status = "pending"
    answered: datetime | None = None
    # Identifies the tool call, so the hooks that run after it can close the request (see `done`).
    key: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "participant": self.participant,
            "summary": self.summary,
            "created": self.created.isoformat(),
            "status": self.status,
            "answered": self.answered.isoformat() if self.answered else None,
        }


def _now() -> datetime:
    return datetime.now(UTC)


class Permissions:
    """Publishes a `permission` event with the request every time it is created or answered."""

    def __init__(
        self,
        participants: Mapping[str, Participant],
        channels: Sequence[Channel] = (),
        *,
        clock: Callable[[], datetime] = _now,
        keep: int = KEEP,
    ) -> None:
        self._participants = participants
        self._channels = channels
        self._clock = clock
        self._keep = keep
        self._items: dict[int, Permission] = {}
        self._next = 1
        self._lock = threading.Lock()

    def request(self, participant: str, summary: str, key: str | None = None) -> Permission:
        p = self._participants.get(participant)
        if p is None or p.type != "agent":
            raise RoomError(f"unknown agent: {participant}")
        with self._lock:
            item = Permission(self._next, participant, summary, self._clock(), key=key)
            self._items[item.id] = item
            self._next += 1
        self._publish(item)
        return item

    def decide(self, id: int, allow: bool) -> Permission:
        return self._answer(id, "allowed" if allow else "denied")

    def release(self, id: int) -> None:
        """The hook stopped waiting: a request still pending was answered elsewhere."""
        with contextlib.suppress(PermissionAnswered):
            self._answer(id, "terminal")

    def done(self, participant: str, key: str) -> None:
        """The tool call ran, so it was allowed in the terminal: Claude Code does not stop the hook then.

        Closes the oldest pending request of that call, if any; the room answers its hook with no decision.
        """
        with self._lock:
            id = next(
                (
                    i.id
                    for i in self._items.values()
                    if i.status == "pending" and (i.participant, i.key) == (participant, key)
                ),
                None,
            )
        if id is not None:
            self.release(id)

    def get(self, id: int) -> Permission:
        with self._lock:
            try:
                return self._items[id]
            except KeyError:
                raise PermissionNotFound(id) from None

    def list(self) -> list[Permission]:
        with self._lock:
            return list(self._items.values())

    def _answer(self, id: int, status: Status) -> Permission:
        with self._lock:
            item = self._items.get(id)
            if item is None:
                raise PermissionNotFound(id)
            if item.status != "pending":
                raise PermissionAnswered(f"permission request #{id} was already answered")
            item = self._items[id] = replace(item, status=status, answered=self._clock())
            answered = [i for i in self._items.values() if i.status != "pending"]
            for old in answered[: max(len(answered) - self._keep, 0)]:
                del self._items[old.id]
        self._publish(item)
        return item

    def _publish(self, item: Permission) -> None:
        event = Event("permission", item.as_dict())
        for channel in self._channels:
            channel.publish(event)
