"""Shared context passed to every adapter's fetch(ctx), plus the control-flow
exceptions adapters use to degrade gracefully instead of erroring."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from core.http import PoliteClient


class SkipSource(Exception):
    """Raised by an adapter that cannot run yet (e.g. missing API key).
    Shows as a yellow health row with the note, not a failure."""
    def __init__(self, note: str):
        super().__init__(note)
        self.note = note


class EmptyPayload(Exception):
    """Raised when an endpoint answered 200 but with none of the expected
    content — likely a format change. Yellow status, prior data kept."""


@dataclass
class RunContext:
    config: dict
    client: PoliteClient
    watch_area: object | None = None   # core.geo.WatchArea, None if boundaries failed
    extras: dict = field(default_factory=dict)

    def source_config(self, name: str) -> dict:
        return self.config["sources"][name]

    def api_key(self, source: str) -> str | None:
        """Read the source's API key from the env var named in config."""
        env_name = self.source_config(source).get("api_key_env")
        if not env_name:
            return None
        return os.environ.get(env_name) or None

    def require_api_key(self, source: str) -> str:
        key = self.api_key(source)
        if not key:
            env_name = self.source_config(source)["api_key_env"]
            raise SkipSource(f"add {env_name} to .env to enable (free key - see README)")
        return key
