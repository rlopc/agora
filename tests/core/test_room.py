from datetime import UTC, datetime, timedelta

import pytest

from agora.core import (
    Participant,
    Room,
    RoomError,
    WakeError,
    extract_mentions,
    format_line,
)
from agora.store.sqlite import SqliteStore

PARTICIPANTS = [
    Participant("user", "User", "human"),
    Participant("claude", "Claude", "agent", "watch"),
    Participant("codex", "Codex", "agent", "codex_queue"),
]


class Clock:
    def __init__(self):
        self.t = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)

    def __call__(self):
        self.t += timedelta(seconds=1)
        return self.t


class FakeWaker:
    def __init__(self, failures=0):
        self.calls = []
        self.failures = failures

    def wake(self, participant, pending):
        if self.failures:
            self.failures -= 1
            raise WakeError("no response")
        self.calls.append((participant.id, [m.id for m in pending]))


class FakeChannel:
    def __init__(self):
        self.events = []

    def publish(self, event):
        self.events.append(event)


@pytest.fixture
def env():
    wakers = {"claude": FakeWaker(), "codex": FakeWaker()}
    channel = FakeChannel()
    sleeps = []
    room = Room(
        PARTICIPANTS,
        SqliteStore(":memory:"),
        [channel],
        wakers,
        clock=Clock(),
        sleep=sleeps.append,
    )
    return room, wakers, channel, sleeps


def test_mentions_valid_unknown_and_without_duplicates():
    valid, unknown = extract_mentions("@Codex and @claude, again @codex; @nobody email a@b.c", {"codex", "claude"})
    assert valid == ("codex", "claude")
    assert unknown == ("nobody",)


def test_format_line():
    room = Room(PARTICIPANTS, SqliteStore(":memory:"))
    m1 = room.send("user", "hello").message
    m2 = room.send("claude", "@codex look", reply_to=m1.id).message
    assert format_line(m1) == "#1 user: hello"
    assert format_line(m2) == "#2 claude→codex (re #1): @codex look"


def test_human_message_wakes_the_mentioned_agent(env):
    room, wakers, channel, _ = env
    result = room.send("user", "@claude review phase 1")
    assert result.message.hops == 0
    assert result.notified == ("claude",)
    assert wakers["claude"].calls == [("claude", [1])]
    assert wakers["codex"].calls == []
    assert [e.type for e in channel.events][:1] == ["message"]


def test_hops_and_limit_between_agents(env):
    room, wakers, _, _ = env
    room.send("user", "@claude start")
    e1 = room.send("claude", "@codex thoughts?")
    e2 = room.send("codex", "@claude a field is missing")
    e3 = room.send("claude", "@codex added")
    assert [e.message.hops for e in (e1, e2, e3)] == [1, 2, 3]
    assert e3.notified == ()
    assert room.state()["limit_reached"] is True
    assert len(wakers["codex"].calls) == 1

    e4 = room.send("user", "@codex go on")
    assert e4.message.hops == 0
    assert e4.notified == ("codex",)
    assert room.state()["limit_reached"] is False


def test_does_not_wake_itself_nor_the_human(env):
    room, wakers, _, _ = env
    result = room.send("claude", "@claude @user note")
    assert result.message.mentions == ("user",)
    assert result.notified == ()
    assert wakers["claude"].calls == []


def test_wakes_batched_until_there_is_activity(env):
    room, wakers, _, _ = env
    room.send("user", "@codex one")
    room.send("user", "@codex two")
    assert wakers["codex"].calls == [("codex", [1])]

    room.pending("codex", activity=True)
    room.send("user", "@codex three")
    assert wakers["codex"].calls[-1] == ("codex", [1, 2, 3])


def test_batched_message_is_woken_when_the_agent_acts_without_having_seen_it(env):
    # Real case (room #20/#21): #2 arrives before the agent acts after the wake for #1;
    # the agent only handles what the wake carried and #2 was left without a wake forever.
    room, wakers, _, _ = env
    room.send("user", "@codex one")
    room.send("user", "@codex two")
    assert wakers["codex"].calls == [("codex", [1])]

    room.mark_read("codex", [1])
    assert wakers["codex"].calls[-1] == ("codex", [2])


def test_send_also_rewakes_the_unseen(env):
    # Real case (room #5/#6): the agent replies to the first one and misses the second.
    room, wakers, _, _ = env
    room.send("user", "@codex one")
    room.send("user", "@codex two")
    room.send("codex", "@user saw the first one", reply_to=1)
    assert wakers["codex"].calls[-1] == ("codex", [2])


def test_what_was_already_shown_in_pending_is_not_rewoken(env):
    room, wakers, _, _ = env
    room.send("user", "@codex one")
    room.send("user", "@codex two")
    room.pending("codex", activity=True)
    room.mark_read("codex", [1])
    assert len(wakers["codex"].calls) == 1


def test_message_arriving_while_the_wake_is_delivered_is_woken_afterwards(env):
    room, _, _, _ = env

    class Slow(FakeWaker):
        def wake(self, participant, pending):
            super().wake(participant, pending)
            if len(self.calls) == 1:
                room.send("user", "@codex two")  # arrives before the wake finishes

    room.wakers["codex"] = slow = Slow()
    room.send("user", "@codex one")
    assert slow.calls == [("codex", [1]), ("codex", [1, 2])]


def test_rewake_respects_hop_limit_and_pause(env):
    room, wakers, _, _ = env
    room.send("user", "@claude start")
    room.send("claude", "@codex one")  # hops 1: wakes
    room.send("codex", "@claude two")  # hops 2
    room.send("claude", "@codex three")  # hops 3: limit, does not wake
    room.mark_read("codex", [2])
    assert wakers["codex"].calls == [("codex", [2])]

    room.set_paused(True)
    room.send("user", "@codex four")
    room.mark_read("codex", [2])
    assert len(wakers["codex"].calls) == 1


def test_after_restart_earlier_messages_are_not_rewoken():
    store = SqliteStore(":memory:")
    Room(PARTICIPANTS, store, wakers={"codex": FakeWaker()}).send("user", "@codex old")
    waker = FakeWaker()
    room = Room(PARTICIPANTS, store, wakers={"codex": waker})
    room.mark_read("codex", [])
    assert waker.calls == []


def test_fetching_pending_without_activity_does_not_count(env):
    room, wakers, _, _ = env
    room.send("user", "@codex one")
    room.pending("codex")
    room.send("user", "@codex two")
    assert len(wakers["codex"].calls) == 1


def test_replying_marks_the_original_read(env):
    room, _, _, _ = env
    m = room.send("user", "@codex question").message
    room.send("codex", "@user answer", reply_to=m.id)
    assert room.pending("codex") == []
    assert [x.id for x in room.pending("user")] == [2]


def test_pause_does_not_wake_and_has_no_retroactive_effect(env):
    room, wakers, _, _ = env
    room.set_paused(True)
    result = room.send("user", "@codex while paused")
    assert result.notified == ()
    room.set_paused(False)
    assert wakers["codex"].calls == []

    room.wake_now("codex")
    assert wakers["codex"].calls == [("codex", [1])]


def test_wake_now_ignores_pause_and_requires_pending(env):
    room, wakers, _, _ = env
    with pytest.raises(RoomError):
        room.wake_now("codex")
    with pytest.raises(RoomError):
        room.wake_now("user")
    room.set_paused(True)
    room.send("user", "@codex hello")
    room.wake_now("codex")
    assert len(wakers["codex"].calls) == 1


def test_retry_and_recorded_failure(env):
    room, wakers, channel, sleeps = env
    wakers["codex"].failures = 1
    room.send("user", "@codex hello")
    assert sleeps == [5.0]
    assert wakers["codex"].calls == [("codex", [1])]
    assert room.state()["participants"]["codex"]["error"] is None

    wakers["codex"].failures = 2
    room.pending("codex", activity=True)
    room.send("user", "@codex another")
    assert room.state()["participants"]["codex"]["error"] == "no response"
    wakes = [e.data for e in channel.events if e.type == "wake"]
    assert wakes[-1] == {"participant": "codex", "ok": False, "error": "no response"}

    # After a failure, the next message tries to wake again even if there is no activity.
    room.send("user", "@codex still there?")
    assert wakers["codex"].calls[-1] == ("codex", [1, 2, 3])


@pytest.mark.parametrize(
    "author, text, reply_to",
    [("nobody", "hello", None), ("user", "   ", None), ("user", "hello", 42)],
)
def test_invalid_requests(env, author, text, reply_to):
    room, _, _, _ = env
    with pytest.raises(RoomError):
        room.send(author, text, reply_to)


def test_unknown_mentions_are_reported(env):
    room, _, _, _ = env
    result = room.send("user", "@gemini hello")
    assert result.unknown == ("gemini",)
    assert result.message.mentions == ()


def test_history(env):
    room, _, _, _ = env
    for i in range(5):
        room.send("user", f"m{i}")
    assert [m.id for m in room.history(limit=2)] == [4, 5]
    assert [m.id for m in room.history(since=1, limit=2)] == [2, 3]


def test_active_listen_and_margin(env, monkeypatch):
    room, _, channel, _ = env
    assert room.listening("claude") is False
    room.record_listen("claude", True)
    assert room.listening("claude") is True
    assert room.state()["participants"]["claude"]["listening"] is True
    assert channel.events[-1].type == "state"

    room.record_listen("claude", False)
    assert room.listening("claude") is True  # brief gap between two waits
    import agora.core.room as module

    now = module.time.monotonic()
    monkeypatch.setattr(module.time, "monotonic", lambda: now + 11)
    assert room.listening("claude") is False


def test_duplicate_participants():
    with pytest.raises(RoomError):
        Room([PARTICIPANTS[0], PARTICIPANTS[0]], SqliteStore(":memory:"))


def test_moderator_resets_hops_and_the_brake_still_applies_between_agents():
    participants = [
        Participant("user", "User", "human"),
        Participant("moderator", "Moderator", "agent", "watch", role="moderator"),
        Participant("aegis", "AEGIS", "agent", "codex_queue"),
        Participant("vista", "AIVista", "agent", "codex_queue"),
    ]
    wakers = {p.id: FakeWaker() for p in participants if p.type == "agent"}
    room = Room(participants, SqliteStore(":memory:"), wakers=wakers, max_hops=2, clock=Clock())
    room.send("user", "@moderator start")
    m = room.send("moderator", "@aegis @vista introduce yourselves")
    assert m.message.hops == 0
    assert set(m.notified) == {"aegis", "vista"}
    e1 = room.send("aegis", "@vista and you?")
    e2 = room.send("vista", "@aegis me too")
    assert [e1.message.hops, e2.message.hops] == [1, 2]
    assert e2.notified == ()
    m2 = room.send("moderator", "@aegis go on")
    assert m2.message.hops == 0
    assert m2.notified == ("aegis",)


def test_user_message_without_mentions_goes_to_the_moderator():
    participants = [
        Participant("user", "User", "human"),
        Participant("moderator", "Moderator", "agent", "watch", role="moderator"),
        Participant("aegis", "AEGIS", "agent", "codex_queue"),
    ]
    wakers = {p.id: FakeWaker() for p in participants if p.type == "agent"}
    room = Room(participants, SqliteStore(":memory:"), wakers=wakers, clock=Clock())
    e = room.send("user", "start phase 0")
    assert e.message.mentions == ("moderator",) and e.notified == ("moderator",)
    assert [m.id for m in room.pending("moderator")] == [e.message.id]
    # With explicit mentions, or if an agent writes, nothing is added.
    assert room.send("user", "@aegis hello").message.mentions == ("aegis",)
    assert room.send("aegis", "no mentions").message.mentions == ()


def test_without_moderator_a_message_without_mentions_wakes_nobody():
    room = Room(PARTICIPANTS, SqliteStore(":memory:"), clock=Clock())
    assert room.send("user", "hello").notified == ()


@pytest.mark.parametrize(
    "participant",
    [
        Participant("moderator", "Moderator", "agent", role="moderators"),
        Participant("user2", "Other", "human", role="moderator"),
    ],
)
def test_invalid_role_is_rejected(participant):
    with pytest.raises(RoomError, match="role"):
        Room([Participant("user", "User", "human"), participant], SqliteStore(":memory:"))
