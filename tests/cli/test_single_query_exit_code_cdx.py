"""CDX carried fix 75f209a68f — `hermes chat -q` must not exit 0 when the turn failed.

The kanban dispatcher spawns non-goal-mode workers as `chat -q` and sees ONLY the exit code.
A clean 0 with no terminal tool call is scored as a WORKER protocol violation, which burns the
task's retries and trips the breaker. During the 2026-08-06 provider outage that produced 277
phantom violations and destroyed 81 tasks whose workers simply could not reach a model.

The fully-quiet (-Q) branch has always set an exit code; the -q branch did not, because
`cli.chat()` returns only the response string. This pins the -q contract, including the
EX_TEMPFAIL sentinel for rate_limit/billing (a quota wall is not a bad card). It exists because
a live A/B against a dead endpoint could not be constructed in an isolated HERMES_HOME — the
provider still resolved — so the behaviour needed a test that does not depend on the network.
"""
import pytest

import cli as cli_mod


class FakeCLI:
    def __init__(self, result):
        self._result = result
        self.console = type("C", (), {"print": staticmethod(lambda *a, **k: None)})()
        self.chatted = False

    def _claim_active_session(self, *_a, **_k):
        return True

    def _show_security_advisories(self):
        pass

    def _print_exit_summary(self, **_k):
        pass

    def chat(self, query, images=None):
        # What hermes_cli/cli_chat_turn_mixin.py does in its `finally:` after every turn.
        self.chatted = True
        self._last_run_result = self._result
        return (self._result or {}).get("final_response", "")


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(cli_mod, "_should_seed_interactive", lambda *a, **k: False)
    monkeypatch.setattr(cli_mod, "_collect_query_images", lambda q, image: (q, []))
    monkeypatch.setattr(cli_mod, "_collect_kanban_task_images", lambda images: [])
    monkeypatch.setattr(cli_mod, "_finalize_single_query", lambda cli: None)
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)


def _run(result):
    fake = FakeCLI(result)
    cli_mod._run_single_query_mode(fake, "do the thing", None, quiet=False, oneshot=False)
    return fake


def test_a_successful_q_turn_returns_normally():
    fake = _run({"final_response": "done", "failed": False})
    assert fake.chatted


def test_a_failed_q_turn_exits_1():
    with pytest.raises(SystemExit) as exc:
        _run({"final_response": "Error: could not reach any provider", "failed": True, "error": "x"})
    assert exc.value.code == 1


@pytest.mark.parametrize("reason", ["rate_limit", "billing"])
def test_a_quota_wall_in_a_kanban_worker_exits_tempfail_not_failure(monkeypatch, reason):
    from hermes_cli.kanban_db import KANBAN_RATE_LIMIT_EXIT_CODE

    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_abc")
    with pytest.raises(SystemExit) as exc:
        _run({"failed": True, "failure_reason": reason})
    assert exc.value.code == KANBAN_RATE_LIMIT_EXIT_CODE == 75


def test_a_quota_wall_outside_a_kanban_worker_is_a_plain_failure(monkeypatch):
    with pytest.raises(SystemExit) as exc:
        _run({"failed": True, "failure_reason": "rate_limit"})
    assert exc.value.code == 1


def test_a_turn_that_recorded_no_result_does_not_invent_a_failure():
    fake = _run(None)
    assert fake.chatted
