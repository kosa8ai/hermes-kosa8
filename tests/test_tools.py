"""Hermes's real terminal and file tools, dispatched through the tool registry with
``terminal.backend: kosa8``, against the fake kosa8 Cloud."""

import json

import kosa8_plugin
import pytest
from agent import terminal_env_registry as reg


@pytest.fixture
def tools(cloud, monkeypatch):
    import model_tools  # noqa: F401  registers the tools dispatched below
    from tools import file_tools, process_registry, terminal_tool

    previous = reg.snapshot_registration("kosa8")

    class Ctx:
        def get_config(self, key, default=None):
            return {"template": "universal-2x4"}.get(key, default)

        def register_terminal_environment_provider(self, provider):
            reg.register_provider(provider)

    kosa8_plugin.register(Ctx())
    monkeypatch.setenv("TERMINAL_ENV", "kosa8")
    monkeypatch.setenv("TERMINAL_CONTAINER_PERSISTENT", "false")
    monkeypatch.setattr(terminal_tool, "_active_environments", {})
    monkeypatch.setattr(terminal_tool, "_last_activity", {})
    monkeypatch.setattr(file_tools, "_file_ops_cache", {})
    monkeypatch.setattr(process_registry.process_registry, "_running", {})
    monkeypatch.setattr(process_registry.process_registry, "_finished", {})
    yield cloud
    from tools.terminal_tool_lifecycle import cleanup_vm
    cleanup_vm("kosa8-task")
    reg.restore_registration("kosa8", reg.get_provider("kosa8"), previous)


def call(name, args):
    from tools.registry import registry
    out = registry.dispatch(name, args, task_id="kosa8-task")
    return json.loads(out) if isinstance(out, str) else out


def test_terminal_and_file_tools_run_in_the_sandbox(tools, workspace):
    r = call("terminal", {"command": "echo from-kosa8 && pwd"})
    assert r.get("exit_code", r.get("returncode")) == 0 and "from-kosa8" in r["output"]
    assert len([b for b in tools.sandboxes.values() if b["state"] != "deleted"]) == 1

    call("write_file", {"path": f"{workspace}/note.txt", "content": "line one\nline two\n"})
    with open(f"{workspace}/note.txt") as f:
        assert f.read() == "line one\nline two\n"

    r = call("read_file", {"path": f"{workspace}/note.txt"})
    assert "line two" in json.dumps(r)

    call("patch", {"path": f"{workspace}/note.txt", "old_string": "line two", "new_string": "line 2"})
    with open(f"{workspace}/note.txt") as f:
        assert "line 2" in f.read()

    r = call("search_files", {"pattern": "line 2", "path": workspace})
    assert "note.txt" in json.dumps(r)

    # Every exec reached kosa8 Cloud through the API, none ran through Hermes's local backend.
    assert tools.exec_count > 0
    assert all(path.startswith("/v1/") for _, path, _ in tools.requests)
