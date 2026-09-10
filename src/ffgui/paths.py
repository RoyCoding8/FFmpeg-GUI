"""ffgui's per-user directories — the TUI's ``paths.py`` with ``_APP = "ffgui"``."""

from __future__ import annotations

import os
import platform
from pathlib import Path

_APP = "ffgui"
_PLATFORM_ALIASES = {
    "windows": "Windows",
    "linux": "Linux",
    "macos": "Darwin",
    "darwin": "Darwin",
}
_XDG = {"cache": ("XDG_CACHE_HOME", ".cache"), "config": ("XDG_CONFIG_HOME", ".config")}
_MAC_DIRS = {"cache": "Library/Caches", "config": "Library/Application Support"}


def system() -> str:
    override = os.environ.get("FFTUI_PLATFORM", "").strip().lower()
    return _PLATFORM_ALIASES.get(override, platform.system())


def app_dir(kind: str) -> Path:
    env, dotted = _XDG[kind]
    home = Path(os.path.expanduser("~"))
    if (plat := system()) == "Darwin":
        return home / _MAC_DIRS[kind] / _APP
    if plat == "Linux" and (base := os.environ.get(env, "").strip()):
        return Path(os.path.expandvars(os.path.expanduser(base))) / _APP
    return home / dotted / _APP


def cache_dir(override: str | os.PathLike[str] | None = None) -> Path:
    if override is None:
        override = os.environ.get("FFGUI_CACHE_DIR", "")
    if not isinstance(override, (str, os.PathLike)):
        raise ValueError(f"cache dir must be a path, got {type(override).__name__}")
    raw = os.fspath(override).strip()
    return Path(os.path.expandvars(os.path.expanduser(raw))) if raw else app_dir("cache")
