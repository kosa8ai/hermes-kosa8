"""Live tests against kosa8 Cloud.

Skipped unless ``KOSA8_LIVE=1`` and ``KOSA8_API_KEY`` are set when pytest starts. Each test makes a
small sandbox (``universal-2x4`` unless ``KOSA8_TEMPLATE`` says otherwise, with a 15 minute ttl as a
backstop) and deletes it at the end.
"""

from __future__ import annotations

import os

import pytest
from conftest import DictState

# Read before the autouse fixture strips KOSA8_API_KEY from the environment.
_KEY = os.environ.get("KOSA8_API_KEY", "")
_LIVE = os.environ.get("KOSA8_LIVE") == "1" and bool(_KEY)
_TEMPLATE = os.environ.get("KOSA8_TEMPLATE", "universal-2x4")

pytestmark = pytest.mark.skipif(not _LIVE, reason="needs KOSA8_LIVE=1 and KOSA8_API_KEY")

WS = "/workspace"


def make_env(**kw):
    from kosa8_plugin.kosa8_env import Kosa8Environment

    kw.setdefault("persistent", False)
    return Kosa8Environment(api_key=_KEY, template=_TEMPLATE, cwd=WS, timeout=60, workspace=WS,
                            persist_dirs=(), ttl_s=900, **kw)


def test_exit_code_output_cwd_and_exports():
    env = make_env()
    try:
        r = env.execute("echo hello; echo oops >&2; exit 3")
        assert r["returncode"] == 3
        assert "hello" in r["output"] and "oops" in r["output"]
        env.execute("mkdir -p sub && cd sub && export K8_TEST_VAR=kept")
        r = env.execute("pwd; echo $K8_TEST_VAR")
        assert r["output"].split() == [f"{WS}/sub", "kept"]
    finally:
        env.cleanup()


def test_stdin_and_large_commands():
    env = make_env()
    try:
        r = env.execute("wc -c", stdin_data="abc\n" * 10)
        assert r["output"].strip() == "40"
        payload = "x" * 200_000
        r = env.execute(f"printf %s '{payload}' | wc -c")
        assert r["returncode"] == 0 and r["output"].strip() == "200000"
        r = env.execute("wc -c", stdin_data="y" * 300_000)
        assert r["output"].strip() == "300000"
    finally:
        env.cleanup()


def test_timeout_answers_124():
    env = make_env()
    try:
        r = env.execute("sleep 30", timeout=3)
        assert r["returncode"] == 124
        assert env.execute("echo alive")["output"].strip().endswith("alive")
    finally:
        env.cleanup()


def test_persistent_sandbox_sleeps_and_wakes_for_the_same_task():
    state = DictState()
    env = make_env(persistent=True, state=state, task_id="live-t1")
    sid = env.sandbox_id
    again = None
    try:
        env.execute("echo keep > kept.txt")
        env.cleanup()
        again = make_env(persistent=True, state=state, task_id="live-t1")
        assert again.sandbox_id == sid
        assert again.execute("cat kept.txt")["output"].strip() == "keep"
    finally:
        # Delete for real: a persistent cleanup only puts the sandbox to sleep.
        target = again or env
        target._client.delete_sandbox(sid)
