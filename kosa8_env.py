"""kosa8 Cloud execution environment.

Each Hermes task runs in one kosa8 Cloud sandbox. Every command is one exec call to the
kosa8 Cloud API (``POST /v1/sandboxes/{id}/exec``); the vendored ``_kosa8`` transport passes
commands and stdin of any size, enforces the timeout inside the sandbox, and uses streamed exec
so long commands are not cut at the API's 90 s buffered limit.

Persistent mode (``terminal.container_persistent``, the default) puts the sandbox to sleep on
cleanup and wakes it for the same task next time, keeping its disk. Otherwise cleanup deletes it.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shlex
import threading
from collections.abc import Callable
from typing import Any

from tools.environments.base import BaseEnvironment

from ._kosa8 import (
    Kosa8Client,
    Kosa8Error,
    Kosa8PaymentRequiredError,
    Kosa8TimeoutError,
    SandboxSession,
    drive,
    files_put,
)

logger = logging.getLogger(__name__)

#: The sandbox directory commands start in, and the one the kosa8 files API reaches.
WORKSPACE = "/workspace"

_STATE_KEY_RE = re.compile(r"[^A-Za-z0-9_.:-]")


def state_key(task_id: str) -> str:
    """A PluginState key for a task's sandbox id (keys allow ``[A-Za-z0-9_.:-]``, 128 chars)."""
    clean = _STATE_KEY_RE.sub("_", task_id or "default")
    if len(clean) > 80 or clean != task_id:
        clean = clean[:40] + "." + hashlib.sha256((task_id or "").encode()).hexdigest()[:16]
    return f"sandbox:{clean}"


class _ExecHandle:
    """ProcessHandle for one kosa8 exec call: runs ``exec_fn() -> (output, exit_code)`` on a thread
    and exposes its output through a pipe, as Hermes's wait loop expects."""

    def __init__(self, exec_fn: Callable[[], tuple[str, int]], cancel_fn: Callable[[], None] | None = None):
        self._cancel_fn = cancel_fn
        self._done = threading.Event()
        self._returncode: int | None = None
        read_fd, write_fd = os.pipe()
        self._stdout = os.fdopen(read_fd, "r", encoding="utf-8", errors="replace")
        self._write_fd = write_fd

        def worker() -> None:
            try:
                output, code = exec_fn()
            except Exception as exc:  # never leave the waiter hanging
                output, code = f"kosa8: {exc}\n", 1
            if self._returncode is None:
                self._returncode = code
            try:
                if output:
                    data = output.encode("utf-8", errors="replace")
                    view = memoryview(data)
                    while view:
                        n = os.write(self._write_fd, view)
                        view = view[n:]
            except OSError:
                pass
            finally:
                try:
                    os.close(self._write_fd)
                except OSError:
                    pass
                self._done.set()

        threading.Thread(target=worker, name="kosa8-exec", daemon=True).start()

    @property
    def stdout(self):
        return self._stdout

    @property
    def returncode(self) -> int | None:
        return self._returncode if self._done.is_set() else None

    def poll(self) -> int | None:
        return self.returncode

    def kill(self) -> None:
        if self._cancel_fn is not None:
            try:
                self._cancel_fn()
            except Exception:
                pass
        if self._returncode is None:
            self._returncode = 130

    def wait(self, timeout: float | None = None) -> int:
        self._done.wait(timeout=timeout)
        return self._returncode if self._returncode is not None else -1


class Kosa8Environment(BaseEnvironment):
    """kosa8 Cloud sandbox execution backend."""

    # stdin reaches the command through the transport (base64 in the exec request, or a staged
    # file when large), not a process pipe.
    _stdin_mode = "payload"
    # The askpass prologue checks sudo -A support inside each command; no separate probe.
    _sudo_askpass_runtime_checked = True
    # A cold sandbox can take a while to answer its first command.
    _snapshot_timeout = 120

    def __init__(self, *, api_key: str, template: str, cwd: str = "/workspace", timeout: int = 60,
                 task_id: str = "default", persistent: bool = True, region: str | None = None,
                 sleep_after_s: int | None = None, ttl_s: int | None = None,
                 base_url: str | None = None, state: Any = None, client: Kosa8Client | None = None,
                 workspace: str = WORKSPACE, persist_dirs: tuple[str, ...] | None = None):
        if not cwd or cwd == "~":
            cwd = workspace
        super().__init__(cwd=cwd, timeout=timeout)
        self._client = client or Kosa8Client(api_key, base_url=base_url, files_root=workspace)
        session_kw: dict[str, Any] = {"workspace": workspace, "shell": "/bin/bash"}
        if persist_dirs is not None:
            session_kw["persist_dirs"] = persist_dirs
        self._session = SandboxSession(**session_kw)
        self._persistent = persistent
        self._task_id = task_id
        self._state = state if persistent else None
        self._state_key = state_key(task_id)
        self._lock = threading.Lock()
        self._sandbox_id: str | None = None

        if self._persistent:
            self._sandbox_id = self._resume()
        if self._sandbox_id is None:
            box = self._client.create_sandbox(template=template, region=region,
                                              sleep_after_s=sleep_after_s, ttl_s=ttl_s)
            self._sandbox_id = box["id"]
            logger.info("kosa8: created sandbox %s for task %s", self._sandbox_id, task_id)
            self._remember(self._sandbox_id)
        self.init_session()

    @property
    def sandbox_id(self) -> str | None:
        return self._sandbox_id

    # --- persistence ------------------------------------------------------------

    def _remember(self, sandbox_id: str | None) -> None:
        if self._state is None:
            return
        try:
            self._state.set(self._state_key, sandbox_id)
        except Exception as exc:
            logger.warning("kosa8: could not record the sandbox for task %s: %s", self._task_id, exc)

    def _resume(self) -> str | None:
        if self._state is None:
            return None
        try:
            sandbox_id = self._state.get(self._state_key)
        except Exception:
            return None
        if not isinstance(sandbox_id, str) or not sandbox_id:
            return None
        try:
            box = self._client.get_sandbox(sandbox_id)
            if box.get("state") in ("sleeping", "stopped", "paused"):
                box = self._client.wake_sandbox(sandbox_id)
            if box.get("state") == "running":
                logger.info("kosa8: resumed sandbox %s for task %s", sandbox_id, self._task_id)
                return sandbox_id
            logger.info("kosa8: recorded sandbox %s is %s; starting a new one", sandbox_id, box.get("state"))
        except Kosa8Error as exc:
            if exc.status not in (404, 410):
                logger.warning("kosa8: could not resume sandbox %s: %s", sandbox_id, exc)
        self._remember(None)
        return None

    # --- execution --------------------------------------------------------------

    def _run_bash(self, cmd_string: str, *, login: bool = False, timeout: int = 120,
                  stdin_data: str | None = None):
        sandbox_id, client, session = self._sandbox_id, self._client, self._session
        cancelled = threading.Event()
        # One merged stream, in order, as on the other backends.
        command = "exec 2>&1\n" + cmd_string
        if login:
            command = "exec 2>&1\nexec /bin/bash -l -c " + shlex.quote(cmd_string)
        stdin = stdin_data.encode("utf-8", "surrogateescape") if stdin_data else None

        def exec_fn() -> tuple[str, int]:
            if sandbox_id is None:
                return ("kosa8: the sandbox has been cleaned up\n", 1)
            op = session.command_op(command, timeout=timeout, stdin=stdin)
            try:
                out = drive(op, lambda req: client.exec(sandbox_id, req), files_put(client, session, sandbox_id))
            except Kosa8TimeoutError:
                return ("", 124)
            except Kosa8PaymentRequiredError as exc:
                hint = exc.agent_message or exc.upgrade_url or ""
                return (f"kosa8: {exc}{(' ' + hint) if hint else ''}\n", 1)
            except Kosa8Error as exc:
                return (f"kosa8: {exc}\n", 1)
            if cancelled.is_set():
                return (out.stdout + out.stderr, 130)
            if out.timed_out:
                return (out.stdout + out.stderr, 124)
            return (out.stdout + out.stderr, out.exit_code if out.exit_code is not None else 1)

        return _ExecHandle(exec_fn, cancel_fn=cancelled.set)

    # --- teardown ---------------------------------------------------------------

    def cleanup(self):
        with self._lock:
            sandbox_id, self._sandbox_id = self._sandbox_id, None
        if sandbox_id is None:
            return
        try:
            if self._persistent:
                try:
                    self._client.sleep_sandbox(sandbox_id)
                    logger.info("kosa8: sandbox %s is asleep (disk kept)", sandbox_id)
                except Kosa8Error as exc:
                    if exc.status in (404, 410):
                        self._remember(None)
                    else:
                        logger.warning("kosa8: could not put sandbox %s to sleep: %s", sandbox_id, exc)
            else:
                try:
                    self._client.delete_sandbox(sandbox_id)
                except Kosa8Error:
                    # The sandbox may still be live: say so, so Hermes does not settle its jobs.
                    with self._lock:
                        self._sandbox_id = sandbox_id
                    raise
                logger.info("kosa8: deleted sandbox %s", sandbox_id)
        finally:
            if self._sandbox_id is None:
                try:
                    self._client.close()
                except Exception:
                    pass
