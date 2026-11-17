"""Kosa8Environment end to end against the fake kosa8 Cloud: Hermes's own BaseEnvironment
execute() (session snapshot, cwd tracking, stdin, timeouts) over the kosa8 exec API."""

import os

import pytest
from conftest import API_KEY, DictState
from kosa8_plugin.kosa8_env import Kosa8Environment, state_key


def make_env(cloud, workspace, **kw):
    kw.setdefault("template", "universal-2x4")
    kw.setdefault("persistent", False)
    return Kosa8Environment(api_key=API_KEY, cwd=workspace, timeout=30, workspace=workspace,
                            persist_dirs=(), **kw)


def live(cloud):
    return [b for b in cloud.sandboxes.values() if b["state"] != "deleted"]


def test_runs_a_command_and_reports_its_exit_code(cloud, workspace):
    env = make_env(cloud, workspace)
    try:
        r = env.execute("echo hello; echo oops >&2; exit 3")
        assert r["returncode"] == 3
        assert "hello" in r["output"] and "oops" in r["output"]
        assert cloud.sandboxes[env.sandbox_id]["template"] == "universal-2x4"
    finally:
        env.cleanup()
    assert live(cloud) == []


def test_cwd_and_exports_carry_over_between_commands(cloud, workspace):
    env = make_env(cloud, workspace)
    try:
        env.execute("mkdir -p sub && cd sub && export K8_TEST_VAR=kept")
        r = env.execute("pwd; echo $K8_TEST_VAR")
        assert r["output"].split() == [os.path.realpath(os.path.join(workspace, "sub")), "kept"] or \
            r["output"].split() == [os.path.join(workspace, "sub"), "kept"]
    finally:
        env.cleanup()


def test_stdin_reaches_the_command(cloud, workspace):
    env = make_env(cloud, workspace)
    try:
        r = env.execute("cat > in.txt && wc -c < in.txt", stdin_data="abc\n" * 10)
        assert r["returncode"] == 0 and r["output"].strip() == "40"
    finally:
        env.cleanup()


def test_a_command_larger_than_one_exec_request_is_staged(cloud, workspace):
    env = make_env(cloud, workspace)
    try:
        payload = "x" * 200_000
        r = env.execute(f"printf %s '{payload}' | wc -c")
        assert r["returncode"] == 0 and r["output"].strip() == "200000"
        big_stdin = "y" * 300_000
        r = env.execute("wc -c", stdin_data=big_stdin)
        assert r["output"].strip() == "300000"
    finally:
        env.cleanup()


def test_timeout_answers_124(cloud, workspace):
    env = make_env(cloud, workspace)
    try:
        r = env.execute("sleep 20", timeout=2)
        assert r["returncode"] == 124
    finally:
        env.cleanup()


def test_buffered_exec_hosts_work_too(cloud, workspace):
    cloud.stream = False
    env = make_env(cloud, workspace)
    try:
        assert env.execute("echo buffered")["output"].strip().endswith("buffered")
    finally:
        env.cleanup()


def test_api_errors_come_back_as_command_output(cloud, workspace):
    env = make_env(cloud, workspace)
    try:
        cloud.fail_exec_with = (402, {"error": "payment_required", "message": "Your plan has ended.",
                                      "agent_message": "Ask the account owner to pick a plan.",
                                      "upgrade_url": "https://app.kosa8.com/billing"})
        r = env.execute("echo hi")
        assert r["returncode"] != 0
        assert "Your plan has ended." in r["output"]
    finally:
        cloud.fail_exec_with = None
        env.cleanup()


def test_persistent_sandbox_sleeps_and_is_woken_for_the_same_task(cloud, workspace):
    state = DictState()
    env = make_env(cloud, workspace, persistent=True, state=state, task_id="t1")
    sid = env.sandbox_id
    env.execute("echo keep > kept.txt")
    env.cleanup()
    assert cloud.sandboxes[sid]["state"] == "sleeping"
    assert state.get(state_key("t1")) == sid

    again = make_env(cloud, workspace, persistent=True, state=state, task_id="t1")
    try:
        assert again.sandbox_id == sid
        assert cloud.sandboxes[sid]["state"] == "running"
        assert again.execute("cat kept.txt")["output"].strip() == "keep"
    finally:
        again.cleanup()

    other = make_env(cloud, workspace, persistent=True, state=state, task_id="t2")
    try:
        assert other.sandbox_id != sid
    finally:
        other.cleanup()


def test_a_deleted_recorded_sandbox_is_replaced(cloud, workspace):
    state = DictState()
    env = make_env(cloud, workspace, persistent=True, state=state, task_id="t1")
    sid = env.sandbox_id
    env.cleanup()
    cloud.sandboxes[sid]["state"] = "deleted"
    again = make_env(cloud, workspace, persistent=True, state=state, task_id="t1")
    try:
        assert again.sandbox_id != sid
        assert state.get(state_key("t1")) == again.sandbox_id
    finally:
        again.cleanup()


def test_cleanup_is_idempotent(cloud, workspace):
    env = make_env(cloud, workspace)
    env.cleanup()
    env.cleanup()
    assert live(cloud) == []


def test_a_failed_delete_is_raised_so_hermes_keeps_the_jobs(cloud, workspace, monkeypatch):
    from kosa8_plugin._kosa8 import Kosa8Error
    env = make_env(cloud, workspace)

    def refuse(*a, **k):
        raise Kosa8Error("boom", status=500)
    monkeypatch.setattr(env._client, "delete_sandbox", refuse)
    with pytest.raises(Kosa8Error):
        env.cleanup()
    assert env.sandbox_id is not None
    monkeypatch.undo()


@pytest.mark.parametrize("task_id", ["default", "a/b c", "x" * 300, ""])
def test_state_keys_are_valid_plugin_state_keys(task_id):
    import re
    key = state_key(task_id)
    assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", key) and ".." not in key
