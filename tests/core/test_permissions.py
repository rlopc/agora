import pytest

from agora.core import Event, Participant, PermissionAnswered, PermissionNotFound, Permissions, RoomError

PARTICIPANTS = {
    "user": Participant("user", "User", "human"),
    "claude": Participant("claude", "Claude", "agent", "watch"),
}


class Recorder:
    def __init__(self):
        self.events: list[Event] = []

    def publish(self, event):
        self.events.append(event)


def test_request_and_decide_publish_the_request():
    channel = Recorder()
    permissions = Permissions(PARTICIPANTS, [channel])
    item = permissions.request("claude", "Bash: make")
    assert item.status == "pending" and item.answered is None
    allowed = permissions.decide(item.id, True)
    assert allowed.status == "allowed" and allowed.answered is not None
    assert [(e.type, e.data["status"]) for e in channel.events] == [
        ("permission", "pending"),
        ("permission", "allowed"),
    ]
    assert permissions.decide(permissions.request("claude", "Bash: rm").id, False).status == "denied"


def test_only_agents_ask():
    permissions = Permissions(PARTICIPANTS)
    for who in ("user", "nobody"):
        with pytest.raises(RoomError, match="unknown agent"):
            permissions.request(who, "Bash: ls")


def test_first_answer_wins():
    permissions = Permissions(PARTICIPANTS)
    item = permissions.request("claude", "Bash: make")
    permissions.release(item.id)
    assert permissions.get(item.id).status == "terminal"
    with pytest.raises(PermissionAnswered):
        permissions.decide(item.id, True)
    decided = permissions.request("claude", "Bash: make")
    permissions.decide(decided.id, False)
    permissions.release(decided.id)  # the hook ends after the room decided: nothing changes
    assert permissions.get(decided.id).status == "denied"
    with pytest.raises(PermissionNotFound):
        permissions.decide(99, True)


def test_keeps_the_pending_and_the_last_answered():
    permissions = Permissions(PARTICIPANTS, keep=2)
    pending = permissions.request("claude", "Bash: wait")
    for n in range(4):
        permissions.release(permissions.request("claude", f"Bash: {n}").id)
    assert [p.summary for p in permissions.list()] == ["Bash: wait", "Bash: 2", "Bash: 3"]
    assert permissions.get(pending.id).status == "pending"


def test_done_closes_the_oldest_pending_request_of_that_call():
    permissions = Permissions(PARTICIPANTS)
    first = permissions.request("claude", "Bash: make", key="k")
    second = permissions.request("claude", "Bash: make", key="k")
    other = permissions.request("claude", "Bash: make", key="other")
    permissions.done("claude", "k")
    assert [permissions.get(i.id).status for i in (first, second, other)] == ["terminal", "pending", "pending"]
    permissions.done("codex", "k")  # another agent, or nothing pending: no change
    permissions.done("claude", "missing")
    assert permissions.get(second.id).status == "pending"
