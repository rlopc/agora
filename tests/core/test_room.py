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
def env(open_store):
    wakers = {"claude": FakeWaker(), "codex": FakeWaker()}
    channel = FakeChannel()
    sleeps = []
    room = Room(
        PARTICIPANTS,
        open_store(),
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


def test_format_line(open_store):
    room = Room(PARTICIPANTS, open_store())
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


def test_after_restart_earlier_messages_are_not_rewoken(open_store):
    store = open_store()
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


def test_duplicate_participants(open_store):
    with pytest.raises(RoomError):
        Room([PARTICIPANTS[0], PARTICIPANTS[0]], open_store())


def test_moderator_resets_hops_and_the_brake_still_applies_between_agents(open_store):
    participants = [
        Participant("user", "User", "human"),
        Participant("moderator", "Moderator", "agent", "watch", role="moderator"),
        Participant("aegis", "AEGIS", "agent", "codex_queue"),
        Participant("vista", "AIVista", "agent", "codex_queue"),
    ]
    wakers = {p.id: FakeWaker() for p in participants if p.type == "agent"}
    room = Room(participants, open_store(), wakers=wakers, max_hops=2, clock=Clock())
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


def test_moderator_turn_limit_waits_for_the_user(open_store):
    participants = [
        Participant("user", "User", "human"),
        Participant("moderator", "Moderator", "agent", "watch", role="moderator"),
        Participant("aegis", "AEGIS", "agent", "codex_queue"),
    ]
    wakers = {p.id: FakeWaker() for p in participants if p.type == "agent"}
    room = Room(participants, open_store(), wakers=wakers, max_moderator_turns=2, clock=Clock())
    room.send("user", "start")
    m1 = room.send("moderator", "@aegis one")
    a1 = room.send("aegis", "@moderator done")
    m2 = room.send("moderator", "@aegis two")
    a2 = room.send("aegis", "@moderator done again")
    assert [m.message.turns for m in (m1, a1, m2, a2)] == [1, 1, 2, 2]
    assert a2.notified == ("moderator",)
    m3 = room.send("moderator", "@aegis three")  # turn 3: over the limit, does not wake
    assert m3.message.turns == 3 and m3.notified == ()
    assert room.state()["turn_limit_reached"] is True
    calls = len(wakers["aegis"].calls)
    room.mark_read("aegis", [m1.message.id, m2.message.id])  # the agent acts: the rewake also respects the limit
    assert len(wakers["aegis"].calls) == calls
    room.wake_now("aegis")  # manual wake ignores the limit
    assert wakers["aegis"].calls[-1][1] == [m3.message.id]
    room.mark_read("aegis", [m3.message.id])
    u = room.send("user", "@moderator go on")
    m4 = room.send("moderator", "@aegis four")
    assert (u.message.turns, m4.message.turns, m4.notified) == (0, 1, ("aegis",))
    assert room.state()["turn_limit_reached"] is False


def test_user_message_without_mentions_goes_to_the_moderator(open_store):
    participants = [
        Participant("user", "User", "human"),
        Participant("moderator", "Moderator", "agent", "watch", role="moderator"),
        Participant("aegis", "AEGIS", "agent", "codex_queue"),
    ]
    wakers = {p.id: FakeWaker() for p in participants if p.type == "agent"}
    room = Room(participants, open_store(), wakers=wakers, clock=Clock())
    e = room.send("user", "start phase 0")
    assert e.message.mentions == ("moderator",) and e.notified == ("moderator",)
    assert [m.id for m in room.pending("moderator")] == [e.message.id]
    # With explicit mentions, or if an agent writes, nothing is added.
    assert room.send("user", "@aegis hello").message.mentions == ("aegis",)
    assert room.send("aegis", "no mentions").message.mentions == ()


def test_without_moderator_a_message_without_mentions_wakes_nobody(open_store):
    room = Room(PARTICIPANTS, open_store(), clock=Clock())
    assert room.send("user", "hello").notified == ()


@pytest.mark.parametrize(
    "participant",
    [
        Participant("moderator", "Moderator", "agent", role="moderators"),
        Participant("user2", "Other", "human", role="moderator"),
    ],
)
def test_invalid_role_is_rejected(participant, open_store):
    with pytest.raises(RoomError, match="role"):
        Room([Participant("user", "User", "human"), participant], open_store())


# Blind rounds

ROUND_PARTICIPANTS = [
    Participant("user", "User", "human"),
    Participant("moderator", "Moderator", "agent", "watch", role="moderator"),
    Participant("proponent", "Proponent", "agent", "watch"),
    Participant("critic", "Critic", "agent", "watch"),
]


@pytest.fixture
def debate(open_store):
    wakers = {p.id: FakeWaker() for p in ROUND_PARTICIPANTS if p.type == "agent"}
    store = open_store()
    room = Room(ROUND_PARTICIPANTS, store, [], wakers, clock=Clock())
    return room, wakers, store


def test_only_the_user_or_the_moderator_opens_a_round_with_two_agents(debate):
    room, _, _ = debate
    with pytest.raises(RoomError, match="only the user or the moderator"):
        room.send("proponent", "@critic @moderator thoughts?", blind=True)
    with pytest.raises(RoomError, match="at least two"):
        room.send("user", "@critic thoughts?", blind=True)
    room.send("user", "@proponent @critic thoughts?", blind=True)
    with pytest.raises(RoomError, match="still open"):
        room.send("moderator", "@proponent @critic again?", blind=True)


def test_answers_are_hidden_from_the_other_agents_until_everyone_replies(debate):
    room, wakers, _ = debate
    opening = room.send("user", "@proponent @critic which database?", blind=True)
    assert opening.notified == ("proponent", "critic")

    answer = room.send("proponent", "SQLite; @critic will say Postgres", reply_to=1)
    # Only the user (who opened it) is mentioned; the critic's mention is held and nobody is woken.
    assert answer.message.mentions == ("user",)
    assert answer.held == ("critic",)
    assert answer.notified == ()
    assert answer.message.hops == 0
    assert [m.id for m in room.pending("critic")] == [1]
    assert [m.id for m in room.history(viewer="critic")] == [1]
    assert [m.id for m in room.history(viewer="proponent")] == [1, 2]
    assert [m.id for m in room.history()] == [1, 2]
    assert room.state()["round"]["waiting"] == ["critic"]

    room.send("critic", "Postgres", reply_to=1)
    assert room.state()["round"] is None
    assert [m.id for m in room.history(viewer="critic")] == [1, 2, 3]
    assert room.pending("critic") == []  # the held mention is not delivered after the close either
    assert wakers["critic"].calls == [("critic", [1])]


def test_the_moderator_is_woken_once_with_all_the_answers(debate):
    room, wakers, _ = debate
    room.send("user", "@moderator compare both options")
    room.send("moderator", "@proponent @critic your position?", reply_to=1, blind=True)
    room.send("proponent", "A", reply_to=2)
    assert room.pending("moderator") == []
    assert wakers["moderator"].calls == [("moderator", [1])]

    room.send("critic", "B", reply_to=2)
    assert wakers["moderator"].calls == [("moderator", [1]), ("moderator", [3, 4])]


def test_the_user_closes_a_round_that_someone_does_not_answer(debate):
    room, wakers, _ = debate
    room.send("moderator", "@proponent @critic your position?", blind=True)
    room.send("proponent", "A", reply_to=1)
    with pytest.raises(RoomError, match="only the user or the moderator"):
        room.close_round("proponent")
    room.close_round("user")
    assert wakers["moderator"].calls == [("moderator", [2])]
    with pytest.raises(RoomError, match="no open blind round"):
        room.close_round("user")

    late = room.send("critic", "B, sorry @proponent", reply_to=1)
    assert late.message.round is None
    assert late.notified == ("proponent",)


def test_a_round_survives_a_restart(debate):
    room, wakers, store = debate
    room.send("moderator", "@proponent @critic your position?", blind=True)
    room.send("proponent", "A", reply_to=1)

    restarted = Room(ROUND_PARTICIPANTS, store, [], wakers, clock=Clock())
    assert restarted.state()["round"]["waiting"] == ["critic"]
    restarted.send("critic", "B", reply_to=1)
    assert wakers["moderator"].calls == [("moderator", [2, 3])]


def test_closing_while_paused_wakes_nobody(debate):
    room, wakers, _ = debate
    room.send("moderator", "@proponent @critic your position?", blind=True)
    room.set_paused(True)
    room.send("proponent", "A", reply_to=1)
    room.send("critic", "B", reply_to=1)
    assert wakers["moderator"].calls == []
    assert [m.id for m in room.pending("moderator")] == [2, 3]
