"""The ``kosa8`` terminal backend provider: classification, health checks and environment creation."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from agent.terminal_env_provider import TerminalEnvironmentProvider

logger = logging.getLogger(__name__)

API_KEY_ENV = "KOSA8_API_KEY"
DEFAULT_TEMPLATE = "universal-2x4"
DEFAULT_SLEEP_AFTER_S = 900
SIGNUP_URL = "https://app.kosa8.com"
DOCS_URL = "https://docs.kosa8.com"
#: The sandbox directory commands start in (kosa8's workspace disk).
WORKSPACE = "/workspace"


def _api_key() -> str:
    """The kosa8 Cloud API key for the active profile, through Hermes's secret scope."""
    from agent.secret_scope import get_secret
    return (get_secret(API_KEY_ENV) or "").strip()


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class Kosa8Provider(TerminalEnvironmentProvider):
    """kosa8 Cloud: each Hermes task gets its own microVM sandbox, reached over HTTPS."""

    # Commands run in the sandbox's own filesystem, not the host's.
    is_remote = True
    is_container = True
    # A persistent sandbox is resumed by its recorded id per task; with
    # container_persistent: false each session gets its own one.
    session_isolated_when_nonpersistent = True

    def __init__(self, get_config: Callable[..., Any] | None = None,
                 get_state: Callable[[], Any] | None = None) -> None:
        self._get_config = get_config
        self._get_state = get_state

    # --- identity -------------------------------------------------------------

    @property
    def name(self) -> str:
        return "kosa8"

    @property
    def display_name(self) -> str:
        return "kosa8 Cloud"

    @property
    def description(self) -> str:
        return "Run commands in a kosa8 Cloud microVM sandbox."

    @property
    def env_description(self) -> str:
        return "a kosa8 Cloud sandbox (Linux microVM)"

    @property
    def default_cwd(self) -> str | None:
        return WORKSPACE

    @property
    def strip_env_keys(self) -> frozenset:
        return frozenset({API_KEY_ENV})

    # --- settings -------------------------------------------------------------

    def setting(self, key: str, default: Any = None) -> Any:
        if self._get_config is None:
            return default
        try:
            value = self._get_config(key, default)
        except Exception:  # a broken config read must not break a UI paint
            return default
        return default if value is None else value

    def settings(self) -> dict[str, Any]:
        ttl_s = _as_int(self.setting("ttl_s", 0), 0)
        return {
            "template": str(self.setting("template", DEFAULT_TEMPLATE) or DEFAULT_TEMPLATE).strip(),
            "region": str(self.setting("region", "") or "").strip() or None,
            "sleep_after_s": max(0, _as_int(self.setting("sleep_after_s", DEFAULT_SLEEP_AFTER_S), DEFAULT_SLEEP_AFTER_S)),
            "ttl_s": ttl_s if ttl_s > 0 else None,
            "base_url": str(self.setting("base_url", "") or "").strip() or None,
        }

    def state(self) -> Any:
        if self._get_state is None:
            return None
        try:
            return self._get_state()
        except Exception:
            return None

    # --- health (no network calls) ---------------------------------------------

    def is_available(self) -> bool:
        try:
            return bool(_api_key())
        except Exception:
            return False

    def check_requirements(self, config: dict[str, Any]) -> bool:
        if self.is_available():
            return True
        logger.error("kosa8 backend selected but %s is not set for this profile. Create a key at %s "
                     "and add it to this profile's .env (or its secret source).", API_KEY_ENV, SIGNUP_URL)
        return False

    def probe(self) -> tuple[str, str]:
        if self.is_available():
            return ("ready", "")
        return ("needs_setup", f"Set {API_KEY_ENV} (create a key at {SIGNUP_URL}).")

    def setup_instructions(self) -> list[str]:
        lines = []
        if not self.is_available():
            lines.append(f"Set {API_KEY_ENV} to a kosa8 Cloud API key from {SIGNUP_URL} "
                         "(kosa8 Cloud is a paid service; plans start at $29/month).")
        lines.append(f"Template: {self.settings()['template']} (change it with "
                     "`hermes config set plugins.entries.kosa8.settings.template <template>`).")
        lines.append(f"Docs: {DOCS_URL}")
        return lines

    def doctor_checks(self) -> list[tuple[bool, str, str]]:
        ok = self.is_available()
        return [(ok, "kosa8 Cloud backend", "(API key set)" if ok else f"({API_KEY_ENV} not set)")]

    # --- environments -----------------------------------------------------------

    def create_environment(self, *, cwd: str, timeout: int, task_id: str = "default",
                           image: str | None = None, container_config: dict[str, Any] | None = None,
                           **kwargs: Any):
        from .kosa8_env import Kosa8Environment
        cc = container_config or {}
        if image:
            logger.info("kosa8: per-task image %r is not used; sandboxes start from the configured template", image)
        api_key = _api_key()
        if not api_key:
            raise ValueError(f"{API_KEY_ENV} is not set for this profile: add it to this profile's .env "
                             f"(or its secret source). Create a key at {SIGNUP_URL}.")
        s = self.settings()
        return Kosa8Environment(
            api_key=api_key, base_url=s["base_url"], template=s["template"], region=s["region"],
            sleep_after_s=s["sleep_after_s"], ttl_s=s["ttl_s"],
            persistent=bool(cc.get("container_persistent", True)), state=self.state(),
            cwd=cwd, timeout=timeout, task_id=task_id, workspace=WORKSPACE)
