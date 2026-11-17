"""The provider through Hermes's real terminal-environment registry: registration, classification,
health checks without network calls, and the secret handling core relies on."""

import kosa8_plugin
import pytest
from agent import terminal_env_registry as reg
from kosa8_plugin.provider import Kosa8Provider


class _Ctx:
    def __init__(self, settings=None):
        self.settings = settings or {}

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def register_terminal_environment_provider(self, provider):
        reg.register_provider(provider)


@pytest.fixture
def registered():
    previous = reg.snapshot_registration("kosa8")
    kosa8_plugin.register(_Ctx({"template": "universal-4x8"}))
    current = reg.get_provider("kosa8")
    yield current
    reg.restore_registration("kosa8", current, previous)


def test_backend_resolves_and_core_reads_its_flags(registered):
    assert registered is not None and registered.name == "kosa8"
    assert "kosa8" in reg.plugin_backend_names()
    assert "kosa8" not in reg.BUILTIN_BACKEND_NAMES
    assert reg.provider_flag("kosa8", "is_remote") is True
    assert reg.provider_flag("kosa8", "is_container") is True
    from tools.approval import _should_skip_container_guards
    assert _should_skip_container_guards("kosa8") is True


def test_settings_come_from_the_plugin_config(registered):
    s = registered.settings()
    assert s["template"] == "universal-4x8"
    assert s["sleep_after_s"] == 900
    assert s["ttl_s"] is None and s["region"] is None and s["base_url"] is None


def test_key_is_stripped_from_subprocesses():
    assert "KOSA8_API_KEY" in Kosa8Provider().strip_env_keys


def test_availability_needs_only_the_key(monkeypatch):
    p = Kosa8Provider()
    assert p.is_available() is False
    assert p.probe()[0] == "needs_setup"
    assert p.check_requirements({}) is False
    monkeypatch.setenv("KOSA8_API_KEY", "k8c_live_x")
    assert p.is_available() is True
    assert p.probe() == ("ready", "")
    assert p.doctor_checks()[0][0] is True


def test_create_without_a_key_explains_what_to_do():
    with pytest.raises(ValueError, match="KOSA8_API_KEY"):
        Kosa8Provider().create_environment(cwd="/workspace", timeout=30)


def test_setup_text_states_the_price_floor_without_promising_a_free_tier():
    text = " ".join(Kosa8Provider().setup_instructions()).lower()
    assert "$29/month" in text
    assert "free" not in text


def test_a_broken_config_read_falls_back_to_defaults():
    def boom(key, default=None):
        raise RuntimeError("config unreadable")
    assert Kosa8Provider(get_config=boom).settings()["template"] == "universal-2x4"
