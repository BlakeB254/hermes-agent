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
        # What hermes_cli/cli_chat_turn_mixin.py does in its `finally:` after every turn
        # (attribute renamed to _last_turn_result upstream in v0.21.4).
        self.chatted = True
        self._last_turn_result = self._result
        return (self._result or {}).get("final_response", "")


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(cli_mod, "_should_seed_interactive", lambda *a, **k: False)
    monkeypatch.setattr(cli_mod, "_collect_query_images", lambda q, image: (q, []))
    monkeypatch.setattr(cli_mod, "_collect_kanban_task_images", lambda images: [])
    monkeypatch.setattr(cli_mod, "_finalize_single_query", lambda cli: None)
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    # v0.21.5: _run_single_query_mode pins the CLI on the global plugin manager
    # (get_plugin_manager()._cli_ref = cli, #67597). Restore it, or the FakeCLI leaks into
    # later tests: execute_code then dispatches through it and returns status=error.
    from hermes_cli.plugins import get_plugin_manager
    _pm = get_plugin_manager()
    monkeypatch.setattr(_pm, "_cli_ref", getattr(_pm, "_cli_ref", None), raising=False)
    # It also sets os.environ["HERMES_SINGLE_QUERY_SESSION"] = "1" in-process (#86878); left set,
    # every later execute_code in the session is BLOCKED as unattended. setenv first so
    # monkeypatch records the original (usually absent) value and restores it on undo.
    monkeypatch.setenv("HERMES_SINGLE_QUERY_SESSION", "0")
    monkeypatch.delenv("HERMES_SINGLE_QUERY_SESSION")


def _run(result, fake=None):
    # v0.21.4: the -q path always exits via SystemExit, so a caller that needs to inspect the
    # fake afterwards must own it — this helper cannot return through a raised exit.
    fake = fake if fake is not None else FakeCLI(result)
    cli_mod._run_single_query_mode(fake, "do the thing", None, quiet=False, oneshot=False)
    return fake


def test_a_successful_q_turn_exits_zero():
    # v0.21.4 changed the contract: the -q path now ALWAYS exits explicitly via
    # exit_single_query (it writes the kanban worker-exit trailer first), where it used to
    # return on success. The carried guarantee is unchanged and now pinned harder: a
    # successful turn must not be booked as a failure.
    fake = FakeCLI({"final_response": "done", "failed": False})
    with pytest.raises(SystemExit) as exc:
        _run(None, fake=fake)
    assert (exc.value.code or 0) == 0
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


def test_a_turn_with_no_result_is_a_failure_not_a_silent_success():
    """v0.21.4 INVERTED the carried expectation here, deliberately, and we take upstream's.

    This test used to assert that a turn recording no result exits 0 ("does not invent a
    failure"). That made sense when _last_turn_result was only set on some paths, so None did
    not imply the turn had not run. Upstream now sets it in _chat_settle_turn (and the carried
    fix sets it again in the turn's finally), so a non-dict result means the turn NEVER RAN —
    credentials or agent init failed. _single_query_exit_code documents that as exit 1.

    Taking upstream is the safer direction: exiting 0 here would report success to the Kanban
    dispatcher for a worker whose agent never started, and the card would be booked complete.
    """
    fake = FakeCLI(None)
    with pytest.raises(SystemExit) as exc:
        _run(None, fake=fake)
    assert exc.value.code == 1
    assert fake.chatted
