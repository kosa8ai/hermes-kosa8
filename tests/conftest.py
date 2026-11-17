"""Test bootstrap: import the plugin directory as the package ``kosa8_plugin``.

The Hermes plugin loader imports a directory plugin as a package (``hermes_plugins.<slug>``) so the
relative imports in ``__init__.py`` resolve; tests mirror that under a stable alias. Hermes core
(``agent``, ``tools``, ``hermes_cli``) must be importable: run in an environment with hermes-agent
installed, or with a hermes-agent checkout on ``PYTHONPATH``.

Nothing here talks to the network: kosa8 Cloud is replaced by ``fake_kosa8.FakeCloud``, which runs
each exec as a local subprocess in a temporary directory that stands in for ``/workspace``.
"""

from __future__ import annotations

import functools
import importlib.util
import sys
from pathlib import Path

import pytest

PACKAGE = "kosa8_plugin"
_ROOT = Path(__file__).resolve().parents[1]
_TESTS = Path(__file__).resolve().parent

sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != _ROOT]
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))


def _load_plugin_package():
    if PACKAGE in sys.modules:
        return sys.modules[PACKAGE]
    spec = importlib.util.spec_from_file_location(
        PACKAGE, _ROOT / "__init__.py", submodule_search_locations=[str(_ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = module
    spec.loader.exec_module(module)
    return module


_load_plugin_package()

API_KEY = "k8c_live_test"


@pytest.fixture(autouse=True)
def _hermes_home(tmp_path, monkeypatch):
    """Every test gets its own profile home, never the real one, and no real kosa8 key."""
    home = tmp_path / "hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("KOSA8_API_KEY", raising=False)
    monkeypatch.delenv("KOSA8_CLOUD_URL", raising=False)
    return home


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    return str(ws)


@pytest.fixture
def cloud(workspace, monkeypatch):
    """A fake kosa8 Cloud; every Kosa8Client the plugin makes talks to it."""
    from fake_kosa8 import FakeCloud
    from kosa8_plugin import kosa8_env, provider

    fake = FakeCloud(stream=True, files_root=workspace)
    real = kosa8_env.Kosa8Client
    monkeypatch.setattr(kosa8_env, "Kosa8Client", functools.partial(real, http_client=fake.client()))
    monkeypatch.setattr(provider, "WORKSPACE", workspace)
    monkeypatch.setenv("KOSA8_API_KEY", API_KEY)
    return fake


class DictState:
    """Stands in for Hermes's PluginState (get/set of JSON values)."""

    def __init__(self):
        self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


class RepoRootIsNotATestPackage:
    """The plugin directory is a package with relative imports; collect it as a plain directory."""

    @pytest.hookimpl(tryfirst=True)
    def pytest_collect_directory(self, path, parent):
        if Path(path).resolve() == _ROOT:
            return pytest.Dir.from_parent(parent, path=path)
        return None


def pytest_configure(config):
    if not config.pluginmanager.has_plugin("kosa8-repo-root"):
        config.pluginmanager.register(RepoRootIsNotATestPackage(), "kosa8-repo-root")
