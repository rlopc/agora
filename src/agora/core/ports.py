"""Ports implemented by the adapters (store, channel and wakers)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Protocol

from agora.core.model import Event, Message, Participant, WakeState


class Store(Protocol):
    def add_message(
        self,
        author: str,
        text: str,
        mentions: Sequence[str],
        reply_to: int | None,
        hops: int,
        created: datetime,
        turns: int = 0,
    ) -> Message: ...

    def get_message(self, id: int) -> Message | None: ...

    def last_message(self) -> Message | None: ...

    def messages(self, since: int = 0, limit: int = 200) -> list[Message]:
        """Messages with id > since, in ascending order."""
        ...

    def last_messages(self, limit: int) -> list[Message]:
        """The last `limit` messages, in ascending order."""
        ...

    def pending(self, participant: str, since: int = 0) -> list[Message]:
        """Messages that mention `participant`, by another author, not read by them and with id > since."""
        ...

    def mark_read(self, participant: str, ids: Iterable[int], when: datetime) -> None: ...

    def record_activity(self, participant: str, when: datetime) -> None: ...

    def record_wake(self, participant: str, when: datetime, error: str | None) -> None: ...

    def wake_state(self, participant: str) -> WakeState: ...

    def paused(self) -> bool: ...

    def set_paused(self, value: bool) -> None: ...


class Channel(Protocol):
    """Destination of the room's events (local web, Slack…)."""

    def publish(self, event: Event) -> None: ...


class WakeError(Exception):
    """The waker could not notify the participant."""


class Waker(Protocol):
    def wake(self, participant: Participant, pending: Sequence[Message]) -> None:
        """Notifies the participant of their pending messages; raises WakeError if it fails."""
        ...
