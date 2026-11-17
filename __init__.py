"""kosa8 — Hermes terminal backend that runs commands in a kosa8 Cloud sandbox.

Select it with ``hermes config set terminal.backend kosa8`` and set ``KOSA8_API_KEY``.
See README.md for what it sends over the network and what it stores.
"""

from __future__ import annotations

import logging

from .provider import Kosa8Provider

logger = logging.getLogger(__name__)


def register(ctx) -> None:
    """Register the kosa8 terminal environment provider on the public Hermes seam."""
    provider = Kosa8Provider(get_config=getattr(ctx, "get_config", None), get_state=lambda: getattr(ctx, "state", None))
    ctx.register_terminal_environment_provider(provider)
    logger.debug("kosa8: registered terminal backend %r", provider.name)
