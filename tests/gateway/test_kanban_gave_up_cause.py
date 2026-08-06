"""The gave_up notification must name the cause that actually tripped.

``gave_up`` is emitted by ONE breaker shared by the spawn-failure, timeout,
crash and clean-exit-protocol-violation paths, but the notification used to
hardcode "gave up after repeated spawn failures" for every one of them.

During the 2026-08-06 provider outage that text was wrong for 305 of 308
events: only 3 were genuine spawn failures, while 277 were workers exiting
rc=0 without calling kanban_complete because they could not reach a model.
Operators spent days chasing a spawn bug that did not exist.
"""
from gateway.kanban_watchers import _gave_up_cause


def test_protocol_violation_names_the_clean_exit_not_spawn():
    msg = _gave_up_cause(
        {
            "trigger_outcome": "crashed",
            "protocol_violations": 6,
            "protocol_violation_limit": 3,
        }
    )
    assert "kanban_complete" in msg
    assert "spawn" not in msg, "clean-exit violations are not spawn failures"


def test_timeout_says_timeout():
    assert "timeout" in _gave_up_cause({"trigger_outcome": "timed_out"}).lower()


def test_crash_without_violations_says_crash():
    msg = _gave_up_cause({"trigger_outcome": "crashed"})
    assert "crash" in msg.lower()
    assert "spawn" not in msg


def test_real_spawn_failure_still_says_spawn():
    assert "spawn" in _gave_up_cause({"trigger_outcome": "spawn_failed"})


def test_unknown_trigger_is_named_not_guessed():
    assert "weird_thing" in _gave_up_cause({"trigger_outcome": "weird_thing"})


def test_missing_payload_does_not_claim_spawn():
    for payload in (None, {}):
        msg = _gave_up_cause(payload)
        assert "gave up" in msg
        assert "spawn" not in msg, "never assert a cause we do not have"
