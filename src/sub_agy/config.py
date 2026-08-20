"""Configuration loading and defaults."""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .schema import EXIT_CODES


@dataclass(frozen=True)
class Config:
    default_model: str = "gemini-3.7-flash"
    default_effort: str = "medium"
    default_timeout: str = "30m"
    max_concurrent: int = 3
    max_retries: int = 1
    auto_approve: bool = False
    agy_bin: str = "agy"

    def timeout_seconds(self) -> int:
        return parse_timeout(self.default_timeout)


DEFAULT_CONFIG_PATH = Path.home() / ".config" / "sub-agy" / "config.toml"


_RE_DURATION = re.compile(r"^(\d+)(s|m|h)$")


def parse_timeout(value: str) -> int:
    """Parse Go-style duration string: Ns/Nm/Nh."""
    value = value.strip()
    match = _RE_DURATION.match(value)
    if not match:
        raise ValueError(f"invalid timeout duration: {value!r}")
    amount, unit = int(match.group(1)), match.group(2)
    if unit == "s":
        return amount
    if unit == "m":
        return amount * 60
    if unit == "h":
        return amount * 3600
    raise ValueError(f"invalid timeout unit: {value!r}")


def load_config(path: Path | None = None) -> Config:
    if path is None:
        env_path = os.environ.get("SUB_AGY_CONFIG")
        if env_path:
            path = Path(env_path)
        else:
            path = DEFAULT_CONFIG_PATH
    if not path.exists():
        return Config()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"invalid config file {path}: {exc}") from exc

    kwargs: dict[str, object] = {}
    if "default_model" in data:
        kwargs["default_model"] = str(data["default_model"])
    if "default_effort" in data:
        kwargs["default_effort"] = str(data["default_effort"])
    if "default_timeout" in data:
        kwargs["default_timeout"] = str(data["default_timeout"])
    if "max_concurrent" in data:
        kwargs["max_concurrent"] = int(data["max_concurrent"])
    if "max_retries" in data:
        kwargs["max_retries"] = int(data["max_retries"])
    if "auto_approve" in data:
        kwargs["auto_approve"] = bool(data["auto_approve"])
    if "agy_bin" in data:
        kwargs["agy_bin"] = str(data["agy_bin"])

    return Config(**kwargs)


def agy_bin_path(config: Config) -> str:
    """Resolve configured agy binary; if bare name, require it on PATH."""
    name = config.agy_bin
    if os.path.isabs(name) or "/" in name or "\\" in name:
        return name
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(directory) / name
        if candidate.exists():
            return str(candidate)
    return name
