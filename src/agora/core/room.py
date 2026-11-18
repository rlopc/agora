"""Room rules: mentions, hops, moderator turns, blind rounds, batching of wakes and pause."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from agora.core.mentions import extract_mentions
from agora.core.model import ROLES, Event, Message, Participant, Round
from agora.core.ports import Channel, Store, WakeError, Waker

MAX_HOPS = 3
MAX_MODERATOR_TURNS = 6


class RoomError(ValueError):
    """Invalid request to the room (unknown author, empty text…)."""


@dataclass(frozen=True)
class SendResult:
    message: Message
    unknown: tuple[str, ...]
    notified: tuple[str, ...]
    held: tuple[str, ...] = ()  # agent mentions not delivered because the message is part of a blind round


def _now() -> datetime:
    return datetime.now(UTC)


def _immediate(task: Callable[[], None]) -> None:
    task()


class Room:
    def __init__(
        self,
        participants: Sequence[Participant],
        store: Store,
        channels: Sequence[Channel] = (),
        wakers: Mapping[str, Waker] | None = None,
        *,
        max_hops: int = MAX_HOPS,
        max_moderator_turns: int = MAX_MODERATOR_TURNS,
        clock: Callable[[], datetime] = _now,
        executor: Callable[[Callable[[], None]], None] = _immediate,
        retry_delay: float = 5.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.participants = {p.id: p for p in participants}
        if len(self.participants) != len(participants):
            raise RoomError("duplicate participant ids")
        for p in participants:
            if p.role is not None and (p.role not in ROLES or p.type != "agent"):
                raise RoomError(f"invalid role for {p.id}: {p.role}")
        self.store = store
        self.channels = list(channels)
        self.wakers = dict(wakers or {})
        self.max_hops = max_hops
        self.max_moderator_turns = max_moderator_turns
        self._clock = clock
        self._executor = executor
        self._retry_delay = retry_delay
        self._sleep = sleep
        # Active listens (long-poll from `agora-watch`) and end of the last one, per participant.
        self._listens: dict[str, int] = {}
        self._listen_end: dict[str, float] = {}
        self._listen_lock = threading.Lock()
        # Id of the last message each agent has seen (in a wake or in `pending`). Whatever was batched
        # above that mark is woken again as soon as the agent acts or the wake in progress finishes.
        # Anything before startup counts as seen: after a restart the backlog is not woken again.
        last = store.last_message()
        self._initial_seen = last.id if last else 0
        self._seen: dict[str, int] = {}
        self._seen_lock = threading.Lock()

    @property
    def human(self) -> Participant | None:
        return next((p for p in self.participants.values() if p.type == "human"), None)

    def add_channel(self, channel: Channel) -> None:
        self.channels.append(channel)

    # Participant operations

    def send(self, author: str, text: str, reply_to: int | None = None, *, blind: bool = False) -> SendResult:
        participant = self._participant(author)
        text = text.strip()
        if not text:
            raise RoomError("the message is empty")
        if reply_to is not None and self.store.get_message(reply_to) is None:
            raise RoomError(f"message #{reply_to} does not exist")

        mentions, unknown = extract_mentions(text, self.participants)
        mentions = tuple(m for m in mentions if m != author)
        if not mentions and participant.type == "human":
            # A message from the user without mentions goes to the moderator, if any; otherwise, to nobody.
            mentions = tuple(p.id for p in self.participants.values() if p.role == "moderator")
        round = self.store.current_round()
        round_participants = self._check_opening(participant, mentions, round) if blind else []
        # Round the message answers: any message by one of its participants while it is open.
        answering = round if round is not None and author in round.participants else None
        last = self.store.last_message()
        hops = 0 if participant.type == "human" or participant.role == "moderator" else (last.hops if last else 0) + 1
        # The moderator resets the hops, so its own messages since the user's last one are counted apart.
        turns = (
            0
            if participant.type == "human"
            else (last.turns if last else 0) + (1 if participant.role == "moderator" else 0)
        )

        held: tuple[str, ...] = ()
        if answering:
            # The answer goes only to whoever opened the round; another agent would be anchored by it.
            held = tuple(m for m in mentions if self.participants[m].type == "agent")
            kept = [m for m in mentions if m not in held] + [answering.author]
            mentions = tuple(m for m in dict.fromkeys(kept) if m != author)
            # Answers are replies to the opening message, not a chain between agents.
            opener = self.store.get_message(answering.opener)
            hops = opener.hops if opener else 0

        now = self._clock()
        message = self.store.add_message(
            author, text, mentions, reply_to, hops, now, turns, answering.id if answering else None
        )
        if blind:
            self.store.open_round(author, message.id, round_participants, now)
        if reply_to is not None:
            self.store.mark_read(author, [reply_to], now)
        if participant.type == "agent":
            self.store.record_activity(author, now)

        self._publish("message", message.as_dict())
        notified = () if answering else tuple(p for p in mentions if self._should_wake(p, message))
        for target in notified:
            self._mark_seen(target, message.id)
            self.store.record_wake(target, now, None)
            self._executor(lambda target=target: self._wake(target))
        if answering and self.store.round_authors(answering.id) >= set(answering.participants):
            self._close(answering)
        self._publish("state", self.state())
        if participant.type == "agent":
            self._rewake(author)
        return SendResult(message, unknown, notified, held)

    def pending(self, participant: str, since: int = 0, *, activity: bool = False) -> list[Message]:
        self._participant(participant)
        if activity:
            self.store.record_activity(participant, self._clock())
        result = self._pending(participant, since)
        if activity and result:
            self._mark_seen(participant, result[-1].id)
        return result

    def mark_read(self, participant: str, ids: Iterable[int]) -> None:
        self._participant(participant)
        now = self._clock()
        self.store.mark_read(participant, list(ids), now)
        self.store.record_activity(participant, now)
        self._publish("state", self.state())
        self._rewake(participant)

    def record_listen(self, participant: str, active: bool) -> None:
        """Called by the channel when each `agora-watch` wait starts and ends."""
        self._participant(participant)
        with self._listen_lock:
            before = self._listening(participant)
            if active:
                self._listens[participant] = self._listens.get(participant, 0) + 1
            else:
                self._listens[participant] = max(self._listens.get(participant, 0) - 1, 0)
                self._listen_end[participant] = time.monotonic()
            changed = before != self._listening(participant)
        if changed:
            self._publish("state", self.state())

    def listening(self, participant: str) -> bool:
        with self._listen_lock:
            return self._listening(participant)

    def _listening(self, participant: str, margin: float = 10.0) -> bool:
        # Between two consecutive waits there is a brief gap: it counts as listening for `margin` s.
        if self._listens.get(participant, 0) > 0:
            return True
        end = self._listen_end.get(participant)
        return end is not None and time.monotonic() - end < margin

    def messages(self, since: int = 0, limit: int = 200, viewer: str | None = None) -> list[Message]:
        """`viewer` None, or the user, sees everything; an agent does not see the others' answers in an open round."""
        return self.store.messages(since, limit, self._viewer(viewer))

    def history(self, since: int | None = None, limit: int = 20, viewer: str | None = None) -> list[Message]:
        if since is None:
            return self.store.last_messages(limit, self._viewer(viewer))
        return self.store.messages(since, limit, self._viewer(viewer))

    def close_round(self, by: str) -> Round:
        """Closes the open blind round before everyone has answered (someone does not answer)."""
        participant = self._participant(by)
        round = self.store.current_round()
        if round is None:
            raise RoomError("there is no open blind round")
        if participant.type != "human" and participant.role != "moderator":
            raise RoomError("only the user or the moderator can close a blind round")
        self._close(round)
        self._publish("state", self.state())
        return round

    # Room control (user)

    def set_paused(self, value: bool) -> None:
        self.store.set_paused(value)
        self._publish("state", self.state())

    def wake_now(self, participant: str) -> None:
        """Manual wake: ignores the pause and the limits."""
        p = self._participant(participant)
        if participant not in self.wakers:
            raise RoomError(f"@{p.id} has no waker")
        if not self._pending(participant):
            raise RoomError(f"@{p.id} has no pending messages")
        self.store.record_wake(participant, self._clock(), None)
        self._executor(lambda: self._wake(participant))

    def state(self) -> dict[str, Any]:
        last = self.store.last_message()
        hops = last.hops if last else 0
        turns = last.turns if last else 0
        round = self.store.current_round()
        participants = {}
        for p in self.participants.values():
            wake = self.store.wake_state(p.id)
            participants[p.id] = {
                "pending": len(self._pending(p.id)),
                "last_wake": wake.last_wake.isoformat() if wake.last_wake else None,
                "last_activity": wake.last_activity.isoformat() if wake.last_activity else None,
                "error": wake.error,
                "listening": self.listening(p.id),
            }
        return {
            "paused": self.store.paused(),
            "max_hops": self.max_hops,
            "hops": hops,
            "limit_reached": hops >= self.max_hops,
            "max_moderator_turns": self.max_moderator_turns,
            "moderator_turns": turns,
            "turn_limit_reached": turns > self.max_moderator_turns,
            "round": self._round_state(round) if round else None,
            "participants": participants,
        }

    # Internals

    def _participant(self, id: str) -> Participant:
        try:
            return self.participants[id]
        except KeyError:
            raise RoomError(f"unknown participant: {id}") from None

    def _viewer(self, id: str | None) -> str | None:
        if id is None:
            return None
        return None if self._participant(id).type == "human" else id

    def _pending(self, target: str, since: int = 0) -> list[Message]:
        return self.store.pending(target, since, self._viewer(target))

    def _check_opening(self, participant: Participant, mentions: Sequence[str], round: Round | None) -> list[str]:
        """Participants of the blind round that the message opens."""
        if participant.type != "human" and participant.role != "moderator":
            raise RoomError("only the user or the moderator can open a blind round")
        if round is not None:
            raise RoomError(f"blind round #{round.id} is still open")
        agents = [m for m in mentions if self.participants[m].type == "agent"]
        if len(agents) < 2:
            raise RoomError("a blind round needs at least two mentioned agents")
        return agents

    def _round_state(self, round: Round) -> dict[str, Any]:
        answered = self.store.round_authors(round.id)
        return {
            "id": round.id,
            "opener": round.opener,
            "author": round.author,
            "participants": list(round.participants),
            "answered": [p for p in round.participants if p in answered],
            "waiting": [p for p in round.participants if p not in answered],
        }

    def _close(self, round: Round) -> None:
        """Reveals the answers and wakes whoever opened the round once, with all of them."""
        self.store.close_round(round.id, self._clock())
        self._publish("round", {"id": round.id, "author": round.author, "participants": list(round.participants)})
        target = round.author
        if self.participants[target].type != "agent" or target not in self.wakers or self.store.paused():
            return
        last = self.store.last_message()
        if last is not None and self._over_limit(last):
            return
        # Direct wake, as `wake_now`: the answers may be older than the last restart, which counts them as seen.
        self.store.record_wake(target, self._clock(), None)
        self._executor(lambda: self._wake(target))

    def _should_wake(self, target: str, message: Message) -> bool:
        participant = self.participants[target]
        if participant.type != "agent" or target not in self.wakers:
            return False
        if self.store.paused() or self._over_limit(message):
            return False
        wake = self.store.wake_state(target)
        if wake.last_wake is None or wake.error:
            return True
        # Batching: if there is already a wake after their last activity, it is not repeated.
        return wake.last_activity is not None and wake.last_activity >= wake.last_wake

    def _over_limit(self, message: Message) -> bool:
        """Stored but wakes nobody: the room waits for the user."""
        return message.hops >= self.max_hops or message.turns > self.max_moderator_turns

    def _wake(self, target: str) -> None:
        participant = self.participants[target]
        waker = self.wakers[target]
        error: str | None = None
        for attempt in range(2):
            pending = self._pending(target)
            if not pending:
                return
            self._mark_seen(target, pending[-1].id)
            try:
                waker.wake(participant, pending)
                error = None
                break
            except WakeError as exc:
                error = str(exc) or exc.__class__.__name__
                if attempt == 0:
                    self._sleep(self._retry_delay)
        if error:
            self.store.record_wake(target, self._clock(), error)
        self._publish("wake", {"participant": target, "ok": error is None, "error": error})
        self._publish("state", self.state())
        if error is None:
            self._rewake(target)  # whatever arrived while this wake was being delivered

    def _mark_seen(self, target: str, id: int) -> None:
        with self._seen_lock:
            self._seen[target] = max(self._seen.get(target, self._initial_seen), id)

    def _rewake(self, target: str) -> None:
        """Wakes for pending messages that batching left unwoken and the agent has not seen yet."""
        participant = self.participants[target]
        if participant.type != "agent" or target not in self.wakers or self.store.paused():
            return
        with self._seen_lock:
            seen = self._seen.get(target, self._initial_seen)
            new = [m.id for m in self._pending(target, seen) if not self._over_limit(m)]
            if not new:
                return
            self._seen[target] = new[-1]
        self.store.record_wake(target, self._clock(), None)
        self._executor(lambda: self._wake(target))

    def _publish(self, type: str, data: Mapping[str, Any]) -> None:
        event = Event(type, data)
        for channel in self.channels:
            channel.publish(event)
