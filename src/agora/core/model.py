"""Entities of the agent room."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

ParticipantType = Literal["human", "agent"]
ROLES = frozenset({"moderator"})


@dataclass(frozen=True)
class Participant:
    id: str
    name: str
    type: ParticipantType
    waker: str | None = None
    options: Mapping[str, Any] = field(default_factory=dict)
    role: str | None = None


@dataclass(frozen=True)
class Message:
    id: int
    author: str
    text: str
    mentions: tuple[str, ...]
    reply_to: int | None
    hops: int
    created: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "author": self.author,
            "text": self.text,
            "mentions": list(self.mentions),
            "reply_to": self.reply_to,
            "hops": self.hops,
            "created": self.created.isoformat(),
        }


@dataclass(frozen=True)
class WakeState:
    """Tracking of wakes sent to a participant, to batch them."""

    participant: str
    last_activity: datetime | None = None
    last_wake: datetime | None = None
    error: str | None = None


@dataclass(frozen=True)
class Event:
    """What the core publishes to the channels: `message`, `state` or `wake`."""

    type: str
    data: Mapping[str, Any]


def format_line(message: Message) -> str:
    """Compact representation for agents: `#3 codex→claude (re #2): text`."""
    target = "→" + ",".join(message.mentions) if message.mentions else ""
    reply = f" (re #{message.reply_to})" if message.reply_to else ""
    return f"#{message.id} {message.author}{target}{reply}: {message.text}"
